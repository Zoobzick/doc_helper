// Run: node --test scripts/test_batch_scans.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/assets/js/batch-scans.js'), 'utf8');

function setup({failFirst = false, cancel = false} = {}) {
    const nodes = {};
    for (const id of ['batch-scans', 'scan-upload-form', 'scan-status', 'scan-progress',
                       'scan-cancel', 'scan-file', 'scan-progress-wrap']) {
        nodes[id] = {events: {}, addEventListener(name, callback) { this.events[name] = callback; },
                     querySelectorAll() { return []; }};
    }
    nodes['batch-scans'].dataset = {startUrl: '/batches/1/scans/start/'};
    nodes['batch-scans'].querySelector = () => ({value: 'csrf'});
    const size = 1.5 * 1024 ** 3;
    const slices = [];
    nodes['scan-file'].files = [{name: 'scan.zip', size, slice(start, end) {
        const blob = {size: Math.min(end, size) - start}; slices.push(blob.size); return blob;
    }}];
    const calls = [];
    let reloads = 0, sends = 0;
    class XHR {
        constructor() { this.upload = {}; }
        open(method, url) { assert.equal(method, 'PUT'); assert.equal(url, 'https://s3.example/part'); }
        abort() { this.onabort(); }
        send(blob) {
            sends++;
            if (cancel) { nodes['scan-cancel'].events.click(); return; }
            if (failFirst && sends === 1) { this.onerror(); return; }
            this.upload.onprogress({loaded: blob.size});
            this.status = 200; this.onload();
        }
    }
    vm.runInNewContext(source, {
        document: {getElementById: id => nodes[id]}, XMLHttpRequest: XHR,
        window: {addEventListener() {}, location: {reload() { reloads++; }}},
        setTimeout: callback => callback(),
        fetch: async (url, options) => {
            assert.equal(options.headers['X-CSRFToken'], 'csrf');
            calls.push(url);
            const payload = url.endsWith('/start/') ? {id: 'scan-uuid', part_size: 16 * 1024 ** 2}
                : url.endsWith('/part/') ? {url: 'https://s3.example/part'} : {ok: true};
            return {ok: true, json: async () => payload};
        },
    });
    return {nodes, calls, slices, get sends() { return sends; }, get reloads() { return reloads; },
            submit: () => nodes['scan-upload-form'].events.submit({preventDefault() {}})};
}

test('1.5 GiB file is sliced into 96 parts without reading the full file', async () => {
    const run = setup();
    await run.submit();
    assert.equal(run.sends, 96);
    assert.equal(run.slices.reduce((a, b) => a + b, 0), 1.5 * 1024 ** 3);
    assert.equal(run.calls.filter(url => url.endsWith('/complete/')).length, 1);
    assert.equal(run.reloads, 1);
});

test('a failed part is retried with a fresh signed URL', async () => {
    const run = setup({failFirst: true});
    await run.submit();
    assert.equal(run.sends, 97);
    assert.equal(run.calls.filter(url => url.endsWith('/part/')).length, 97);
    assert.equal(run.reloads, 1);
});

test('cancel aborts the request and cleans the multipart upload', async () => {
    const run = setup({cancel: true});
    await run.submit();
    assert.equal(run.sends, 1);
    assert.equal(run.calls.filter(url => url.endsWith('/delete/')).length, 1);
    assert.equal(run.calls.filter(url => url.endsWith('/complete/')).length, 0);
    assert.equal(run.reloads, 0);
    assert.equal(run.nodes['scan-status'].textContent, 'Загрузка отменена.');
});
