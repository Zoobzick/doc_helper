/* Recover an unfinished create form; saved acts and their status live on the server. */
(() => {
    const form = document.getElementById("act-form");
    if (!form || form.dataset.mode !== "create") return;
    const key = form.dataset.recoveryKey;
    const offer = document.getElementById("act-recovery-offer");
    const status = document.getElementById("act-recovery-status");
    const token = form.elements.namedItem("recovery_token");
    if (!token.value) token.value = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    let pending = null;
    let dirty = false;
    let timer;

    function read() {
        return JSON.parse(localStorage.getItem(key) || "null");
    }

    function snapshot() {
        return {
            version: 1, token: token.value, savedAt: Date.now(),
            fields: Array.from(form.elements).filter(el =>
                el.name && !["csrfmiddlewaretoken", "recovery_token"].includes(el.name) &&
                !["file", "button", "submit"].includes(el.type)
            ).map(el => ({name: el.name, value: el.value, checked: el.checked})),
            projects: Array.from(document.querySelectorAll(".project-chip")).map(el =>
                ({id: el.dataset.id, label: el.dataset.label})),
            approvals: Array.from(document.querySelectorAll("#approvalsTable tr[data-approval-id]")).map(el => el.dataset.approvalId),
            hasFiles: Array.from(form.querySelectorAll('input[type="file"]')).some(el => el.files.length > 0),
        };
    }

    function save() {
        clearTimeout(timer);
        if (!dirty || pending) return;
        try {
            const existing = read();
            // Do not silently overwrite another tab's unfinished form.
            if (existing && existing.token !== token.value) {
                status.textContent = "В другой вкладке сохранена другая форма. Автосохранение этой вкладки приостановлено.";
                return;
            }
            localStorage.setItem(key, JSON.stringify(snapshot()));
            status.textContent = "Ввод сохранён в этом браузере. Файлы после восстановления нужно выбрать повторно.";
        } catch (_) {
            status.textContent = "Не удалось сохранить ввод в браузере. Не закрывайте страницу до сохранения акта.";
        }
    }

    function changed() {
        dirty = true;
        clearTimeout(timer);
        timer = setTimeout(save, 300);
    }

    try {
        const saved = read();
        if (saved?.version === 1 && Array.isArray(saved.fields) && form.dataset.bound !== "1") {
            pending = saved;
            offer.hidden = false;
        }
    } catch (_) {
        status.textContent = "Восстановление ввода недоступно в этом браузере.";
    }

    document.getElementById("act-recovery-restore").addEventListener("click", () => {
        if (!pending) return;
        const saved = pending;
        // Create dynamic rows from trusted page templates, never from stored HTML.
        for (const [prefix, table, template] of [
            ["mat", "materials-table", "material-empty-form"],
            ["att", "attachments-table", "attachment-empty-form"],
        ]) {
            const count = Number(saved.fields.find(f => f.name === `${prefix}-TOTAL_FORMS`)?.value || 0);
            if (!Number.isInteger(count) || count < 0 || count > 1000) {
                status.textContent = "Не удалось восстановить сохранённую форму: неверное количество строк.";
                return;
            }
            const total = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
            while (Number(total.value) < count) window.__actsAddRow(table, template, prefix);
        }
        document.querySelectorAll("#approvalsTable tr[data-approval-id]").forEach(row => row.remove());
        (saved.approvals || []).filter(id => /^\d+$/.test(id)).forEach(id => window.__actsAddApprovalRow(id, ""));
        window.__actsRestoreProjects((saved.projects || []).filter(p => /^\d+$/.test(p.id)));
        for (const field of saved.fields) {
            if (["csrfmiddlewaretoken", "recovery_token", "projects", "approvals"].includes(field.name)) continue;
            let input = Array.from(form.elements).find(el => el.name === field.name);
            if (!input && /^(mat|att)-\d+-DELETE$/.test(field.name)) {
                const prefix = field.name.replace(/DELETE$/, "");
                const rowField = Array.from(form.elements).find(el => el.name.startsWith(prefix));
                if (rowField) {
                    input = document.createElement("input");
                    input.type = "hidden";
                    input.name = field.name;
                    rowField.closest("tr").appendChild(input);
                }
            }
            if (!input || ["file", "button", "submit"].includes(input.type)) continue;
            input.value = field.value;
            if (["checkbox", "radio"].includes(input.type)) input.checked = !!field.checked;
            if (/^(mat|att)-\d+-DELETE$/.test(field.name) &&
                (input.type === "checkbox" ? input.checked : input.value === "on")) {
                input.closest("tr").style.display = "none";
            }
        }
        token.value = saved.token;
        pending = null;
        offer.hidden = true;
        document.getElementById("id_number").dispatchEvent(new Event("input", {bubbles: true}));
        window.__actsRefreshStates();
        window.__actsRefreshOriginalControls();
        dirty = true;
        save();
        status.textContent = "Ввод восстановлен. Проверьте данные; выбранные ранее файлы прикрепите повторно.";
    });

    document.getElementById("act-recovery-discard").addEventListener("click", () => {
        try {
            if (read()?.token === pending?.token) localStorage.removeItem(key);
            pending = null;
            offer.hidden = true;
            status.textContent = "Сохранённая копия удалена.";
            if (dirty) save();
        } catch (_) {
            status.textContent = "Не удалось удалить сохранённую копию.";
        }
    });
    form.addEventListener("input", changed);
    form.addEventListener("change", changed);
    // Row/project selection may change hidden inputs without input/change events.
    document.addEventListener("click", event => {
        if (event.target.closest("#act-recovery-offer")) return;
        if (event.target.closest("#act-form, #passportModal, #approvalsChooseModal")) changed();
    });
    form.addEventListener("submit", () => { dirty = true; save(); });
    window.addEventListener("pagehide", save);
    document.addEventListener("visibilitychange", () => { if (document.hidden) save(); });
    window.addEventListener("pageshow", event => {
        if (event.persisted) dirty = false;
    });
})();
