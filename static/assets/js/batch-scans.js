(() => {
    "use strict";
    const root = document.getElementById("batch-scans");
    if (!root) return;
    const form = document.getElementById("scan-upload-form");
    const status = document.getElementById("scan-status");
    const csrf = root.querySelector("[name=csrfmiddlewaretoken]")?.value;
    let active = false, cancelled = false, xhr = null, scanId = null;
    const startUrl = root.dataset.startUrl;
    const actionUrl = action => startUrl.replace(/start\/$/, `${scanId}/${action}/`);

    async function api(url, data = {}) {
        const response = await fetch(url, {
            method: "POST", credentials: "same-origin",
            headers: {"Content-Type": "application/json", "X-CSRFToken": csrf},
            body: JSON.stringify(data),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || "Не удалось выполнить запрос. Проверьте подключение и вход в приложение.");
        return payload;
    }
    const progress = document.getElementById("scan-progress");
    function uploadPart(url, blob, offset, total) {
        return new Promise((resolve, reject) => {
            xhr = new XMLHttpRequest();
            xhr.open("PUT", url);
            xhr.timeout = 30 * 60 * 1000;
            xhr.upload.onprogress = event => {
                const percent = Math.min(99, Math.floor((offset + event.loaded) / total * 100));
                progress.value = percent;
                status.textContent = `Загрузка: ${percent}%. Не закрывайте страницу.`;
            };
            xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error("S3 не принял часть файла."));
            xhr.onerror = () => reject(new Error("Не удалось связаться с хранилищем. Проверьте сеть; если ошибка повторяется, сообщите администратору."));
            xhr.ontimeout = () => reject(new Error("Превышено время загрузки части файла."));
            xhr.onabort = () => reject(new Error("Загрузка отменена."));
            xhr.send(blob);
        });
    }
    function busy(value) {
        active = value;
        root.querySelectorAll("button[type=submit], input[type=file], .js-scan-delete").forEach(el => el.disabled = value);
        document.getElementById("scan-cancel").hidden = !value;
    }
    window.addEventListener("beforeunload", event => {
        if (active) { event.preventDefault(); event.returnValue = ""; }
    });
    document.getElementById("scan-cancel")?.addEventListener("click", () => {
        cancelled = true;
        if (xhr) xhr.abort();
        status.textContent = "Отменяем загрузку…";
    });
    form?.addEventListener("submit", async event => {
        event.preventDefault();
        if (active) return;
        const file = document.getElementById("scan-file").files[0];
        if (!file) return;
        if (!file.size || file.size > 5 * 1024 ** 3) {
            status.textContent = "Выберите непустой файл размером до 5 ГБ."; return;
        }
        busy(true); cancelled = false; scanId = null;
        document.getElementById("scan-progress-wrap").hidden = false;
        progress.value = 0;
        status.textContent = "Подготавливаем загрузку…";
        let completing = false;
        try {
            const upload = await api(startUrl, {name: file.name, size: file.size});
            scanId = upload.id;
            for (let offset = 0, number = 1; offset < file.size; offset += upload.part_size, number++) {
                const blob = file.slice(offset, offset + upload.part_size);
                for (let attempt = 0; attempt < 3; attempt++) {
                    if (cancelled) throw new Error("Загрузка отменена.");
                    try {
                        const {url} = await api(actionUrl("part"), {number});
                        if (cancelled) throw new Error("Загрузка отменена.");
                        await uploadPart(url, blob, offset, file.size);
                        break;
                    } catch (error) {
                        if (cancelled || attempt === 2) throw error;
                        status.textContent = "Соединение прервано. Повторяем загрузку части…";
                        await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)));
                    }
                }
            }
            if (cancelled) throw new Error("Загрузка отменена.");
            completing = true;
            document.getElementById("scan-cancel").hidden = true;
            status.textContent = "Все части переданы. Проверяем и сохраняем файл…";
            // Completion is idempotent, including a lost response from S3.
            for (let attempt = 0; ; attempt++) {
                try { await api(actionUrl("complete")); break; }
                catch (error) { if (attempt === 2) throw error; }
            }
            progress.value = 100;
            active = false;
            window.location.reload();
        } catch (error) {
            let cleanupFailed = false;
            if (scanId && !completing) {
                try { await api(actionUrl("delete")); } catch (_) { cleanupFailed = true; }
            }
            status.textContent = (cancelled ? "Загрузка отменена." : error.message) +
                (completing ? " Не удалось подтвердить сохранение. Обновите страницу и проверьте файл; незавершённую загрузку можно удалить и повторить." : "") +
                (cleanupFailed ? " Не удалось очистить загрузку. Обновите страницу и удалите незавершённую запись." : "");
        } finally {
            busy(false); xhr = null;
        }
    });
    root.querySelectorAll(".js-scan-delete").forEach(button => button.addEventListener("click", async () => {
        if (!window.confirm("Удалить этот скан и его файл из хранилища?")) return;
        button.disabled = true;
        try { await api(button.dataset.url); window.location.reload(); }
        catch (error) { status.textContent = error.message; button.disabled = false; }
    }));
})();
