// Run: node scripts/test_batch_share.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/assets/js/batch-share.js'), 'utf8');

function setup({clipboardFails = false, serverError = false} = {}) {
    const nodes = {
        button: {}, '.js-share-status': {},
        '.js-share-url': {focus() { this.focused = true; }, select() { this.selected = true; }},
        '[name=csrfmiddlewaretoken]': {value: 'csrf'},
    };
    let handler;
    const form = {action: '/batches/7/share/', querySelector: key => nodes[key],
                  addEventListener: (_, callback) => { handler = callback; }};
    const copied = [], requests = [];
    vm.runInNewContext(source, {
        document: {querySelectorAll: () => [form]},
        navigator: {clipboard: {writeText: async text => {
            if (clipboardFails) throw new Error('Denied');
            copied.push(text);
        }}},
        fetch: async (url, options) => {
            requests.push(url);
            assert.equal(options.method, 'POST');
            assert.equal(options.headers['X-CSRFToken'], 'csrf');
            return {ok: !serverError, json: async () => serverError ? {error: 'Нет сканов'} : {url: 'https://example.test/cloud/random/'}};
        },
    });
    return {nodes, copied, requests, submit: () => handler({preventDefault() {}})};
}

test('Share copies the public page URL', async () => {
    const run = setup();
    await run.submit();
    assert.deepEqual(run.copied, ['https://example.test/cloud/random/']);
    assert.match(run.nodes['.js-share-status'].textContent, /скопирована/);
    assert.equal(run.nodes.button.disabled, false);
});

test('clipboard denial reveals and selects the URL', async () => {
    const run = setup({clipboardFails: true});
    await run.submit();
    const field = run.nodes['.js-share-url'];
    assert.equal(field.hidden, false);
    assert.equal(field.selected, true);
    assert.equal(field.value, 'https://example.test/cloud/random/');
});

test('server errors do not copy a link', async () => {
    const run = setup({serverError: true});
    await run.submit();
    assert.equal(run.copied.length, 0);
    assert.equal(run.nodes['.js-share-status'].textContent, 'Нет сканов');
});

test('double click does not create concurrent shares', async () => {
    const run = setup();
    const pending = run.submit();
    await run.submit();
    await pending;
    assert.equal(run.requests.length, 1);
});
