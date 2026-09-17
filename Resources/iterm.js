// Invoked by freeparking.py with a JSON request. No UI scripting or global shortcuts.
function run(argv) {
    const request = JSON.parse(argv[0]);
    const app = Application('com.googlecode.iterm2');

    function windows() {
        // iTerm retains closed windows for Undo; these have a null tab list.
        // They are not live terminal windows and must not break discovery.
        return app.running() ? app.windows().filter(w => w.tabs() !== null) : [];
    }
    function getWindow(id) {
        const found = windows().find(w => String(w.id()) === String(id));
        if (!found) throw new Error('That iTerm window is no longer open. Refresh Free Parking.');
        return found;
    }
    function sessions(w) {
        return w.tabs().flatMap(t => t.sessions());
    }
    function checkedWindow() {
        const w = getWindow(request.windowId);
        const actual = sessions(w).map(s => s.id());
        if (JSON.stringify(actual) !== JSON.stringify(request.sessionIds)) {
            throw new Error('The tabs changed. Nothing else was closed. Refresh Free Parking.');
        }
        return w;
    }

    function tabTitle(tab, session) {
        // Prefer an explicit tab title; keep iTerm's session name as fallback.
        try { const title = tab.title(); if (title && title.trim()) return title; }
        catch (_) { /* Older iTerm versions may not expose tab.title. */ }
        return session.name();
    }

    if (request.action === 'list') {
        return JSON.stringify(windows().map(w => ({
            id: String(w.id()),
            title: w.name(),
            tabs: w.tabs().map((t, index) => ({
                index: index,
                sessions: t.sessions().map(s => ({id: s.id(), tty: s.tty(), title: tabTitle(t, s)}))
            }))
        })));
    }
    if (request.action === 'check') {
        checkedWindow();
        return JSON.stringify({ok: true});
    }
    if (request.action === 'close') {
        const w = checkedWindow();
        w.select();
        app.activate();
        // iTerm's window AppleScript handler uses performClose:, preserving its
        // configured confirmation. No force flag, preferences, or quit command.
        w.close();
        return JSON.stringify({ok: true});
    }
    if (request.action === 'focus') {
        const w = getWindow(request.windowId);
        const present = new Set(sessions(w).map(s => s.id()));
        if (!Array.isArray(request.sessionIds) || !request.sessionIds.length ||
            !request.sessionIds.every(id => present.has(id))) {
            throw new Error('A saved tab closed or moved. The car has been kept.');
        }
        // iTerm's own select handler; no keystrokes or input broadcasting.
        w.select();
        app.activate();
        return JSON.stringify({ok: true});
    }
    if (request.action === 'create') {
        let w, tab;
        if (request.windowId) {
            w = getWindow(request.windowId);
            tab = w.createTabWithDefaultProfile({command: request.command});
        } else {
            w = app.createWindowWithDefaultProfile({command: request.command});
            tab = w.currentTab();
        }
        app.activate();
        return JSON.stringify({windowId: String(w.id()), sessionId: tab.currentSession().id()});
    }
    throw new Error('Unknown Free Parking operation.');
}
