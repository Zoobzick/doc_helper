// Run: node scripts/test_batch_share.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/assets/js/batch-share.js'), 'utf8');

function setup({clipboardFails = false, serverError = false} = {}) {
    const nodes = {
        button: {setAttribute() {}, removeAttribute() {}},
        '[name=csrfmiddlewaretoken]': {value: 'csrf'},
    };
    let handler;
    const form = {action: '/batches/7/share/', querySelector: key => nodes[key],
                  addEventListener: (_, callback) => { handler = callback; }};
    const copied = [], requests = [], notifications = [];
    vm.runInNewContext(source, {
        document: {querySelectorAll: () => [form]},
        window: {showDocToast: (...args) => notifications.push(args)},
        navigator: {clipboard: {writeText: async text => {
            if (clipboardFails) throw new Error('Denied');
            copied.push(text);
        }}},
        fetch: async (url, options) => {
            requests.push(url);
            assert.equal(options.method, 'POST');
            assert.equal(options.headers['X-CSRFToken'], 'csrf');
            return {ok: !serverError, status: serverError ? 400 : 200, json: async () => serverError ? {error: 'Нет сканов'} : {url: 'https://example.test/cloud/random/'}};
        },
    });
    return {nodes, copied, requests, notifications, submit: () => handler({preventDefault() {}})};
}

test('Share copies the public page URL', async () => {
    const run = setup();
    await run.submit();
    assert.deepEqual(run.copied, ['https://example.test/cloud/random/']);
    assert.match(run.notifications[0][0], /скопирована/);
    assert.equal(run.notifications[0][1], 'success');
    assert.equal(run.nodes.button.disabled, false);
});

test('clipboard denial puts the manual copy URL in a notification', async () => {
    const run = setup({clipboardFails: true});
    await run.submit();
    assert.equal(run.notifications[0][1], 'warning');
    assert.equal(run.notifications[0][2].copyValue, 'https://example.test/cloud/random/');
});

test('server errors do not copy a link', async () => {
    const run = setup({serverError: true});
    await run.submit();
    assert.equal(run.copied.length, 0);
    assert.equal(run.notifications[0][0], 'Нет сканов');
    assert.equal(run.notifications[0][1], 'warning');
});

test('double click does not create concurrent shares', async () => {
    const run = setup();
    const pending = run.submit();
    await run.submit();
    await pending;
    assert.equal(run.requests.length, 1);
});
