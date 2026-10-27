// Only immutable-target operations. No typed input, AX scripting, force close,
// preference writes, title-based identity or global quit.
function run(argv) {
    const request = JSON.parse(argv[0]);
    const app = Application('com.googlecode.iterm2');
    const windows = () => app.running() ? app.windows().filter(w => w.tabs() !== null) : [];
    const sessions = w => w.tabs().flatMap(t => t.sessions());
    function variable(s, name) {
        try { return s.variable({named:name}) || ''; } catch (_) { return ''; }
    }
    function title(t, s) {
        try { const value = t.title(); if (value) return value; } catch (_) {}
        return s.name();
    }
    function exact() {
        const w = windows().find(w => String(w.id()) === request.windowId);
        if (!w || w.tabs().some(t => t.sessions().length !== 1) ||
            JSON.stringify(sessions(w).map(s => s.id())) !== JSON.stringify(request.sessionIds)) {
            throw new Error('The exact target changed. No window operation was performed.');
        }
        return w;
    }
    if (request.action === 'list') {
        return JSON.stringify(windows().map(w => ({id:String(w.id()), tabs:w.tabs().map(t => ({
            sessions:t.sessions().map(s => ({id:s.id(), tty:s.tty(), title:title(t,s),
                receipt:variable(s,'user.freeParkingReceipt')}))
        }))})));
    }
    if (request.action === 'close') {
        let w;
        try { w = exact(); } catch (error) { return JSON.stringify({notSubmitted:String(error)}); }
        w.close(); // Normal confirmation. Never force or approve it ourselves.
        return JSON.stringify({acknowledged:true});
    }
    if (request.action === 'create') {
        let w, t;
        try {
            if (typeof request.command !== 'string' || !request.command) throw new Error('Missing launch specification.');
            if (request.windowId !== null) w = exact();
        } catch (error) { return JSON.stringify({notSubmitted:String(error)}); }
        if (request.windowId !== null) {
            t = w.createTabWithDefaultProfile({command:request.command});
        } else {
            w = app.createWindowWithDefaultProfile({command:request.command});
            t = w.currentTab();
        }
        return JSON.stringify({windowId:String(w.id()),sessionId:t.currentSession().id()});
    }
    throw new Error('Unknown operation.');
}
