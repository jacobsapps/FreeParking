// Pure adapter checks. Node VM fakes only; no Apple events or real terminals.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('Resources/iterm.js', 'utf8');
const session = {id:()=> 'fixture-session', tty:()=> '/dev/fixture', name:()=> 'Fallback'};
let selected = 0, closed = 0;
const live = {id:()=>1, name:()=> 'Fixture', tabs:()=>[{title:()=> 'Task title', sessions:()=>[session]}], select:()=>selected++, close:()=>closed++};
const undo = {id:()=>2, name:()=> 'Retained closed window', tabs:()=>null};
const app = {running:()=>true, windows:()=>[undo,live], activate:()=>{}};
const context=vm.createContext({Application:()=>app});vm.runInContext(source,context);
function call(request){return JSON.parse(context.run([JSON.stringify(request)]));}
const rows=call({action:'list'});
assert.equal(rows.length,1);assert.equal(rows[0].id,'1');
assert.equal(rows[0].tabs[0].sessions[0].title,'Task title');
assert.throws(()=>call({action:'close',windowId:'2',sessionIds:['fixture-session']}));
assert.throws(()=>call({action:'close',windowId:'1',sessionIds:['wrong-session']}));
assert.equal(closed,0);
call({action:'focus',windowId:'1',sessionIds:['fixture-session']});assert.equal(selected,1);
call({action:'close',windowId:'1',sessionIds:['fixture-session']});assert.equal(closed,1);
console.log('JXA adapter checks passed: retained Undo windows, saved titles, exact-window/session guards.');
