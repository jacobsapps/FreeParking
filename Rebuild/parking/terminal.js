// Only immutable-target operations. No typed input, AX scripting, force close,
// preference writes, title-based identity or global quit.
function run(argv) {
    const request = JSON.parse(argv[0]);
    const app = Application('com.googlecode.iterm2');
    // Enumeration proxies may be indexed by front-to-back position. Rebind
    // every window to its immutable native ID before reading or mutating it.
    const windows = () => app.running() ? app.windows.id().map(id => app.windows.byId(id)).filter(w => w.tabs() !== null) : [];
    // Collection reads are single AppleEvents. Reading every session property
    // individually was multiplying both latency and opportunities for churn.
    const layout = w => w.tabs.sessions.id();
    const topology = raw => raw.map(w => [w.id, w.tabs.map(t => t.sessions.map(s => s.id))]).sort((a,b) => a[0].localeCompare(b[0]));
    function variable(s, name) {
        try { return s.variable({named:name}) || ''; } catch (_) { return ''; }
    }
    function title(t, s) {
        try { const value = t.title(); if (value) return value; } catch (_) {}
        return s.name();
    }
    function inventory(w) {
        const metadata = request.metadata !== false;
        const ids = layout(w);
        const ttys = w.tabs.sessions.tty();
        const names = metadata ? w.tabs.sessions.name() : ids.map(group => group.map(() => ''));
        let titles;
        try { titles = metadata ? w.tabs.title() : []; } catch (_) { titles = []; }
        let bounds = {};
        try { if (metadata) bounds = w.bounds(); } catch (_) {}
        return {id:String(w.id()), bounds:bounds, tabs:ids.map((group,i) => ({
            sessions:group.map((sid,j) => {
                const s = w.tabs[i].sessions[j];
                return {id:sid, tty:ttys[i][j], title:titles[i] || names[i][j],
                    name:metadata ? variable(s,'autoName') : '', receipt:variable(s,'user.freeParkingReceipt')};
            })
        }))};
    }
    function exact() {
        const w = app.windows.byId(Number(request.windowId));
        const groups = layout(w);
        if (String(w.id()) !== request.windowId || groups.some(g => g.length !== 1) ||
            JSON.stringify(groups.flat()) !== JSON.stringify(request.sessionIds)) {
            const error = new Error('The exact target changed. No window operation was performed.');
            error.details = {expectedWindowId:request.windowId, expectedSessionIds:request.sessionIds,
                observedWindowId:String(w.id()), observedSessionGroups:groups};
            throw error;
        }
        return w;
    }
    if (request.action === 'list') {
        const original = windows();
        const before = original.map(w => ({id:String(w.id()), tabs:layout(w).map(ids => ({sessions:ids.map(id => ({id}))}))}));
        const raw = original.map(inventory);
        const after = windows().map(w => ({id:String(w.id()), tabs:layout(w).map(ids => ({sessions:ids.map(id => ({id}))}))}));
        const expected = JSON.stringify(topology(raw));
        if (JSON.stringify(topology(before)) !== expected || JSON.stringify(topology(after)) !== expected) {
            throw new Error('FP_READ_RACE: iTerm tabs changed during inspection. Read a fresh inventory.');
        }
        return JSON.stringify(raw);
    }
    if (request.action === 'close') {
        let w;
        try { w = exact(); } catch (error) { return JSON.stringify({notSubmitted:String(error), details:error.details || null}); }
        w.close(); // Normal confirmation. Never force or approve it ourselves.
        return JSON.stringify({acknowledged:true});
    }
    if (request.action === 'create') {
        let w, t;
        try {
            if (typeof request.command !== 'string' || !request.command) throw new Error('Missing launch specification.');
            if (request.windowId !== null) w = exact();
        } catch (error) { return JSON.stringify({notSubmitted:String(error), details:error.details || null}); }
        if (request.windowId !== null) {
            t = w.createTabWithDefaultProfile({command:request.command});
        } else {
            w = app.createWindowWithDefaultProfile({command:request.command});
            t = w.currentTab();
        }
        // Capture identity before any presentation write. A failed title or
        // geometry update must never turn a successful launch into a retry.
        const result = {windowId:String(w.id()),sessionId:t.currentSession().id(),presentationWarnings:[]};
        try {
            if (request.title) {
                try { t.title = request.title; }
                catch (_) { t.currentSession().name = request.name || request.title; }
            }
        }
        catch (error) { result.presentationWarnings.push('Tab title: '+String(error)); }
        try {
            const b = request.bounds;
            if (b && ['x','y','width','height'].every(k => typeof b[k] === 'number') && b.width > 0 && b.height > 0) w.bounds = b;
        } catch (error) { result.presentationWarnings.push('Window position: '+String(error)); }
        return JSON.stringify(result);
    }
    throw new Error('Unknown operation.');
}
