// Usage: node scripts/test_act_form.cjs <rendered creation-form HTML>
// The HTML must include act_form.html's content and scripts blocks.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

(async () => {
    const html = fs.readFileSync(process.argv[2], 'utf8');
    const browser = await chromium.launch({headless: true, channel: 'msedge'});
    try {
        const context = await browser.newContext();
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let serverError = false;
        let showSuccess = false;
        await page.route('**/*', route => {
            const url = new URL(route.request().url());
            if (url.pathname.endsWith('act-recovery.js')) return route.fulfill({contentType:'text/javascript', body:fs.readFileSync('static/assets/js/act-recovery.js','utf8')});
            if (route.request().resourceType() === 'document') {
                if (showSuccess) {
                    const detail = fs.readFileSync('templates/acts_app/act_detail.html','utf8').replace(/\r\n/g,'\n');
                    const start = detail.indexOf('<script>\n        // Clear recovery');
                    const body = detail.slice(start, detail.indexOf('</script>',start) + 9).replace('{{ request.user.pk }}','99');
                    return route.fulfill({contentType:'text/html; charset=utf-8',body});
                }
                const body = serverError ? html.replace('id="id_number">', 'id="id_number"><div class="invalid-feedback d-block">Старая ошибка номера</div>') : html;
                return route.fulfill({contentType:'text/html; charset=utf-8', body});
            }
            if (route.request().resourceType() === 'fetch') return route.fulfill({json:{results:[]}});
            return route.fulfill({body:''});
        });
        async function open() {
            await page.goto('http://localhost/acts/create/');
            await page.evaluate(() => {
                document.getElementById('act-form').addEventListener('submit', e => {
                    window.testBlocked = e.defaultPrevented;
                    e.preventDefault();
                });
                window.__actsRestoreProjects([{id:'1',label:'Тестовый проект'}]);
            });
        }
        async function fill() {
            for (const [id,value] of Object.entries({id_number:'A-1',id_act_date:'2026-10-21',id_work_name:'Работы',id_work_start_date:'2026-10-20',id_work_end_date:'2026-10-21','id_att-0-doc_date':'2026-10-21','id_att-0-sheets_count':'1'})) {
                await page.locator('#'+id).fill(value);
            }
        }
        async function submit() {
            return page.evaluate(() => {
                document.getElementById('act-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
                return window.testBlocked;
            });
        }
        await open();
        await fill();
        assert.equal(await page.locator('#id_is_draft').isChecked(),false);
        await page.locator('#add-attachment').click();
        assert.equal(await submit(),true);
        assert.equal(await page.locator('#act-submit-errors').isVisible(),true);
        await page.locator('#attachments-table .attachment-row:not(.scheme-row) .js-remove-row').click();
        assert.equal(await submit(),false,'deleted attachment must not block submission');
        await page.locator('#add-material').click();
        assert.equal(await submit(),true);
        await page.locator('#materials-table .material-row .js-remove-row').click();
        assert.equal(await submit(),false,'deleted material must not block submission');
        console.log('PASS: invalid fields block visibly; removed rows allow submission');

        await page.locator('#add-material').click();
        await page.locator('#id_mat-1-manual_name').fill('Бетон');
        await page.locator('#id_mat-1-concrete_volume_m3').fill('25');
        await page.locator('#id_is_draft').check();
        await page.evaluate(() => window.__actsAddApprovalRow('12','Согласование'));
        await page.locator('[name="approval_sheets_12"]').fill('3');
        await submit(); // saves even an invalid submission, without clearing recovery
        await page.reload();
        assert.equal(await page.locator('#act-recovery-offer').isVisible(),true);
        await page.locator('#act-recovery-restore').click();
        assert.equal(await page.locator('#id_number').inputValue(),'A-1');
        assert.equal(await page.locator('#id_is_draft').isChecked(),true);
        assert.equal(await page.locator('#id_mat-1-manual_name').inputValue(),'Бетон');
        assert.equal(await page.locator('#id_mat-1-concrete_volume_m3').inputValue(),'25');
        assert.equal(await page.locator('[name="approval_sheets_12"]').inputValue(),'3');
        assert.equal(await page.locator('.project-chip').getAttribute('data-label'),'Тестовый проект');
        assert.equal(await page.locator('#id_mat-0-manual_name').isVisible(),false);
        console.log('PASS: recovery preserves fields, status, projects, approvals, added/deleted rows');

        const savedToken = await page.locator('[name="recovery_token"]').inputValue();
        showSuccess = true;
        await page.goto('http://localhost/acts/detail/?saved_form=another-tab');
        assert.notEqual(await page.evaluate(() => localStorage.getItem('act-create-v1:99')),null);
        await page.goto('http://localhost/acts/detail/?saved_form='+savedToken);
        assert.equal(await page.evaluate(() => localStorage.getItem('act-create-v1:99')),null);
        showSuccess = false;
        console.log('PASS: success clears only the matching recovery copy');

        // Server feedback is rendered before initialization, then corrected by the user.
        await page.evaluate(() => localStorage.clear());
        serverError = true;
        await open();
        await fill();
        assert.equal(await submit(),false,'corrected server error must allow retry');
        assert.equal(await page.locator('.invalid-feedback.d-block').count(),0);
        console.log('PASS: corrected server error no longer blocks retries');
        await page.reload();
        await page.locator('#act-recovery-discard').click();
        assert.equal(await page.evaluate(() => localStorage.getItem('act-create-v1:99')),null);
        console.log('PASS: recovery copy can be discarded');
        assert.deepEqual(errors,[]);
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode=1; });
