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

    function validBounds(b) {
        return b && ['x', 'y', 'width', 'height'].every(k => typeof b[k] === 'number' && Number.isFinite(b[k])) &&
            b.width > 0 && b.height > 0;
    }
    function visibleBounds(saved) {
        if (!validBounds(saved)) throw new Error('Invalid saved window size. The car has been kept.');
        ObjC.import('AppKit');
        const screens = $.NSScreen.screens;
        const top = screens.objectAtIndex(0).frame.origin.y + screens.objectAtIndex(0).frame.size.height;
        const frames = [];
        for (let i = 0; i < screens.count; i++) {
            const f = screens.objectAtIndex(i).visibleFrame;
            frames.push({x: f.origin.x, y: top - f.origin.y - f.size.height,
                         width: f.size.width, height: f.size.height});
        }
        // Keep the exact frame when it still fits. After a display disconnect,
        // use the closest available screen rather than strand a window offscreen.
        const overlap = f => Math.max(0, Math.min(saved.x + saved.width, f.x + f.width) - Math.max(saved.x, f.x)) *
                             Math.max(0, Math.min(saved.y + saved.height, f.y + f.height) - Math.max(saved.y, f.y));
        const screen = frames.sort((a, b) => overlap(b) - overlap(a))[0];
        const width = Math.min(saved.width, screen.width), height = Math.min(saved.height, screen.height);
        return {x: Math.max(screen.x, Math.min(saved.x, screen.x + screen.width - width)),
                y: Math.max(screen.y, Math.min(saved.y, screen.y + screen.height - height)), width, height};
    }

    if (request.action === 'list') {
        return JSON.stringify(windows().map(w => ({
            id: String(w.id()),
            title: w.name(),
            bounds: w.bounds(),
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
    if (request.action === 'set-title' || request.action === 'set-titles') {
        const titles = request.action === 'set-title' ? [request] : request.titles;
        if (!Array.isArray(titles) || !titles.length) throw new Error('No saved titles were provided.');
        const seen = new Set();
        const updates = titles.map(item => {
            const w = getWindow(item.windowId);
            const tabs = w.tabs().filter(t => t.sessions().length === 1 && t.sessions()[0].id() === item.sessionId);
            if (tabs.length !== 1 || typeof item.title !== 'string' || seen.has(item.sessionId)) {
                throw new Error('A restored tab changed. No titles were altered.');
            }
            seen.add(item.sessionId);
            return {tab: tabs[0], title: item.title};
        });
        // Assign the tab title, not the session name or terminal input. This
        // preserves Unicode/custom titles and never participates in broadcasting.
        for (const {tab, title} of updates) {
            try { tab.title = title; }
            catch (_) { tab.sessions()[0].name = title; }
            if (tabTitle(tab, tab.sessions()[0]) !== title) {
                throw new Error('iTerm could not restore the saved title. Enable its Python API and install its Python runtime (Scripts menu), then retry. The car is kept.');
            }
        }
        return JSON.stringify({ok: true});
    }
    if (request.action === 'set-bounds') {
        const w = checkedWindow();
        const target = visibleBounds(request.bounds);
        w.bounds = target;
        let actual = w.bounds();
        for (let i = 0; i < 4 && ['x', 'y', 'width', 'height'].some(k => Math.abs(actual[k] - target[k]) > 1); i++) {
            $.NSThread.sleepForTimeInterval(0.05);
            actual = w.bounds();
        }
        if (!validBounds(actual) || ['x', 'y', 'width', 'height'].some(k => Math.abs(actual[k] - target[k]) > 1)) {
            throw new Error('iTerm did not retain the saved window size. The car has been kept; retry after the window finishes opening.');
        }
        return JSON.stringify({ok: true, bounds: actual});
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
