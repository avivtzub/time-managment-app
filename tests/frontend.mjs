// Offline browser-logic regressions. Run with Node: node tests/frontend.mjs
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const html = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
let passed = 0;
function setup() {
    const nodes = new Map();
    const listeners = new Map();
    let document;
    function node(id = '') {
        const classes = new Set();
        return {id, value: '', checked: false, disabled: false, textContent: '', innerHTML: '', style: {}, dataset: {}, children: [],
            classList: {add: (...xs) => xs.forEach(x => classes.add(x)), remove: (...xs) => xs.forEach(x => classes.delete(x)),
                contains: x => classes.has(x), toggle: (x, force) => {const on = force ?? !classes.has(x); on ? classes.add(x) : classes.delete(x); return on;}},
            append(...items) {this.children.push(...items)}, appendChild(item) {this.children.push(item)},
            replaceChildren(...items) {this.children = items}, setAttribute(name, value) {this[name] = String(value)}, removeAttribute(name) {delete this[name]},
            addEventListener(name, fn) {listeners.set(`${id}:${name}`, fn)},
            focus() {document.activeElement = this}, contains(other) {return this === other || this.children.includes(other)},
            querySelectorAll() {return []}, querySelector() {return node()}, closest(selector) {if (selector === '.hidden') return this.classList.contains('hidden') ? this : null; return this.parentElement || node()},
            get parentElement() {return this._parent ||= node()}, get offsetParent() {return this.classList.contains('hidden') ? null : this.parentElement},
            scrollIntoView() {}, get isConnected() {return true}
        };
    }
    const get = id => {if (!nodes.has(id)) nodes.set(id, node(id)); return nodes.get(id)};
    document = {getElementById: get, createElement: () => node(), activeElement: null, body: node('body'),
        querySelector(selector) {return get(selector)}, querySelectorAll() {return []},
        addEventListener(name, fn) {listeners.set(name, fn)}};
    const calls = [];
    let responder = () => ({status: 200, body: []});
    const context = vm.createContext({document, console, Date, URLSearchParams, Intl, setTimeout, clearTimeout,
        FullCalendar: {Calendar: function(element, options) {this.options = options; this.render = () => {}; this.refetchEvents = () => {}; this.updateSize = () => {}; }},
        window: {addEventListener() {}, location: {origin: 'http://localhost'}, innerWidth: 1200, myCalendar: {refetchEvents() {}, getEvents() {return []}}},
        confirm: () => true, requestAnimationFrame: fn => fn(),
        fetch: async (url, options = {}) => {calls.push({url, options}); const result = await responder(url, options); return {ok: result.status < 400, status: result.status, json: async () => result.body}}});
    vm.runInContext(script, context, {filename: 'index.html'});
    return {context, calls, get, listeners, document, setResponder(fn) {responder = fn}, eval(code) {return vm.runInContext(code, context)}};
}
async function check(name, callback) {await callback(); console.log(`PASS ${name}`); passed++;}
await check('HTTP errors reject with server explanation', async () => {
    const app = setup(); app.setResponder(() => ({status: 409, body: {detail: 'Calendar conflict'}}));
    await assert.rejects(app.context.request('/tasks'), /Calendar conflict/);
});
await check('failed creation retains input and re-enables button', async () => {
    const app = setup(); app.get('taskTitle').value = 'Study at 15:00';
    app.setResponder(() => ({status: 422, body: {detail: 'Invalid task'}}));
    await app.context.addTask();
    assert.equal(app.get('taskTitle').value, 'Study at 15:00');
    assert.equal(app.get('button[onclick="addTask()"]').disabled, false);
});
await check('failed drag reverts and never reports success', async () => {
    const app = setup(); app.setResponder(() => ({status: 409, body: {detail: 'Conflict'}}));
    let reverted = 0, locked = false;
    await app.context.updateEventTimeOnServer({event: {id: '7', start: new Date('2026-11-20T12:00Z'), end: new Date('2026-11-20T13:00Z'), extendedProps: {is_proposed: true}, setExtendedProp() {locked = true}}, revert() {reverted++}});
    assert.equal(reverted, 1); assert.equal(locked, false);
});
await check('scheduled lock save does not PATCH away proposal', async () => {
    const app = setup(); const event = {id: '7', title: 'Study', start: new Date('2026-11-20T12:00Z'), end: new Date('2026-11-20T13:00Z'), extendedProps: {is_proposed: true, locked: true}};
    app.context.openEditModal(event); app.get('modalLocked').checked = false;
    await app.context.saveFromModal();
    const writes = app.calls.filter(call => call.options.method);
    assert.equal(writes.length, 1); assert.equal(writes[0].options.method, 'PUT'); assert.match(writes[0].url, /\/7\/lock$/);
});
await check('scheduled time save uses one time update without PATCH', async () => {
    const app = setup(); app.context.openEditModal({id: '7', title: 'Study', start: new Date('2026-11-20T12:00Z'), end: new Date('2026-11-20T13:00Z'), extendedProps: {is_proposed: true}});
    app.get('modalStart').value = '2026-11-20T17:00'; app.get('modalEnd').value = '2026-11-20T18:00';
    await app.context.saveFromModal();
    const writes = app.calls.filter(call => call.options.method);
    assert.equal(writes.length, 1); assert.equal(writes[0].options.method, 'PUT'); assert.match(writes[0].url, /\/7\/time$/);
});
await check('pending preference save PATCHes only changed constraints', async () => {
    const app = setup(); app.context.openTaskPreferences({id: 9, title: 'Study', time_mode: 'fixed', preferred_start_time: '15:00'});
    app.get('modalTimeMode').value = 'preferred'; await app.context.saveFromModal();
    const writes = app.calls.filter(call => call.options.method);
    assert.equal(writes.length, 1); assert.equal(writes[0].options.method, 'PATCH');
});
await check('pending count uses actual tasks and clears authority on load failure', async () => {
    const app = setup();
    app.setResponder(() => ({status: 200, body: [{id: 1, title: 'A', duration_minutes: 30, preferred_time: 'Any', location_context: 'Anywhere'}, {id: 2, title: 'B', duration_minutes: 60, preferred_time: 'Any', location_context: 'Anywhere'}]}));
    await app.context.fetchTasks(); assert.equal(String(app.get('pendingCount').textContent), '2');
    app.setResponder(() => ({status: 503, body: {detail: 'Offline'}}));
    await app.context.fetchTasks(); assert.equal(app.get('pendingCount').textContent, '—');
    assert.equal(app.get('tasksList')['aria-busy'], 'false');
});
await check('modal receives focus and returns focus to opener', async () => {
    const app = setup(); const opener = app.get('opener'); opener.focus();
    app.context.openTaskPreferences({id: 9, title: 'Study'});
    assert.equal(app.document.activeElement, app.get('modalTimeMode'));
    app.context.closeModal(); assert.equal(app.document.activeElement, opener);
    assert.equal(app.get('editModal').classList.contains('hidden'), true);
});
await check('modal traps forward and reverse Tab and closes on Escape', async () => {
    const app = setup(); app.listeners.get('DOMContentLoaded')();
    const first = app.get('first'), last = app.get('last');
    app.document.querySelectorAll = () => [first, last];
    app.context.openTaskPreferences({id: 9, title: 'Study'});
    let prevented = false; last.focus();
    app.listeners.get('keydown')({key: 'Tab', shiftKey: false, preventDefault() {prevented = true}});
    assert.equal(app.document.activeElement, first); assert.equal(prevented, true);
    app.listeners.get('keydown')({key: 'Tab', shiftKey: true, preventDefault() {}});
    assert.equal(app.document.activeElement, last);
    app.listeners.get('keydown')({key: 'Escape', preventDefault() {}});
    assert.equal(app.get('editModal').classList.contains('hidden'), true);
});
await check('calendar requests visible range and counts only proposed events', async () => {
    const app = setup(); app.listeners.get('DOMContentLoaded')();
    app.setResponder(() => ({status: 200, body: [{id: 'google:1'}, {id: '7', extendedProps: {is_proposed: true}}]}));
    let events;
    await app.context.window.myCalendar.options.events({startStr: '2026-12-01', endStr: '2026-12-08'}, value => events = value, error => {throw error});
    assert.equal(events.length, 2); assert.match(app.get('proposalCount').textContent, /^1 /);
    assert.ok(app.calls.some(call => call.url.includes('start=2026-12-01') && call.url.includes('end=2026-12-08')));
});
await check('failed modal deletion reports error inside active dialog', async () => {
    const app = setup(); app.context.openTaskPreferences({id: 9, title: 'Study'});
    app.setResponder(() => ({status: 409, body: {detail: 'Resolve sync first'}}));
    await app.context.deleteFromModal();
    assert.match(app.get('modalError').textContent, /Resolve sync first/);
    assert.equal(app.get('editModal').classList.contains('hidden'), false);
    assert.equal(app.get('button[onclick="deleteFromModal()"]').disabled, false);
});
await check('proposed event keyboard activation opens accessible dialog', async () => {
    const app = setup(); app.listeners.get('DOMContentLoaded')();
    const event = {id: '7', title: 'Study', start: new Date('2026-11-20T12:00Z'), end: new Date('2026-11-20T13:00Z'), extendedProps: {is_proposed: true}};
    const el = app.get('calendarEvent');
    app.context.window.myCalendar.options.eventDidMount({event, el});
    assert.equal(el.role, 'button'); assert.equal(el.tabindex, '0');
    app.listeners.get('calendarEvent:keydown')({key: 'Enter', preventDefault() {}, stopPropagation() {}});
    assert.equal(app.document.activeElement, app.get('modalStart'));
});
await check('composer Enter preserves newline and Ctrl Enter submits', async () => {
    const app = setup(); app.listeners.get('DOMContentLoaded')(); app.get('taskTitle').value = 'Study';
    let prevented = false;
    app.listeners.get('taskTitle:keydown')({key: 'Enter', preventDefault() {prevented = true}});
    assert.equal(prevented, false); assert.equal(app.calls.filter(c => c.options.method === 'POST').length, 0);
    app.setResponder((url) => ({status: 200, body: url.endsWith('/tasks') ? [] : {message: 'Added'}}));
    app.listeners.get('taskTitle:keydown')({key: 'Enter', ctrlKey: true, preventDefault() {prevented = true}});
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(prevented, true); assert.equal(app.calls.filter(c => c.options.method === 'POST').length, 1);
});
await check('mobile calendar starts in day view', async () => {
    const app = setup(); app.context.window.innerWidth = 390; app.listeners.get('DOMContentLoaded')();
    assert.equal(app.context.window.myCalendar.options.initialView, 'timeGridDay');
});
await check('pending modal deletion blocks save and close until completion', async () => {
    const app = setup(); app.context.openTaskPreferences({id: 9, title: 'Study'});
    let release;
    app.setResponder((url, options) => options.method === 'DELETE'
        ? new Promise(resolve => {release = resolve}) : {status: 200, body: []});
    const deleting = app.context.deleteFromModal();
    await Promise.resolve();
    app.context.closeModal();
    assert.equal(app.get('editModal').classList.contains('hidden'), false);
    await app.context.saveFromModal();
    assert.equal(app.calls.filter(call => call.options.method).length, 1);
    release({status: 200, body: {message: 'Deleted'}});
    await deleting;
    assert.equal(app.get('editModal').classList.contains('hidden'), true);
});
await check('modal pending disables controls and restores prior disabled states', async () => {
    const app = setup(); const editable = app.get('modalEnd'), disabled = app.get('modalTimeMode');
    disabled.disabled = true; app.document.querySelectorAll = () => [editable, disabled];
    app.context.setModalPending(true);
    assert.equal(editable.disabled, true); assert.equal(disabled.disabled, true);
    assert.equal(app.get('#editModal .dialog')['aria-busy'], 'true');
    app.context.setModalPending(false);
    assert.equal(editable.disabled, false); assert.equal(disabled.disabled, true);
    assert.equal(app.get('#editModal .dialog')['aria-busy'], 'false');
});
console.log(`${passed} frontend behavior checks passed`);




