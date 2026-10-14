(() => {
    "use strict";
    const root = document.getElementById("batch-scans");
    if (!root) return;
    const form = document.getElementById("scan-upload-form");
    const status = document.getElementById("scan-status");
    const csrf = root.querySelector("[name=csrfmiddlewaretoken]")?.value;
    let active = false, cancelled = false, xhr = null, scanId = null;
    const fileInput = document.getElementById("scan-file");
    const dropzone = document.getElementById("scan-dropzone");
    const fileName = document.getElementById("scan-file-name");
    function validateFile(file) {
        if (!file || !/\.(pdf|zip|7z|rar|tar|gz)$/i.test(file.name)) {
            return "Выберите PDF или архив ZIP, 7Z, RAR, TAR, GZ.";
        }
        if (!file.size || file.size > 5 * 1024 ** 3) {
            return "Выберите непустой файл размером до 5 ГБ.";
        }
        return "";
    }
    function showSelection() {
        const file = fileInput.files[0];
        const error = file ? validateFile(file) : "";
        if (error) fileInput.value = "";
        fileName.textContent = file && !error ? file.name : "или нажмите, чтобы выбрать файл";
        status.textContent = error;
    }
    fileInput?.addEventListener("change", showSelection);
    for (const name of ["dragenter", "dragover"]) {
        dropzone?.addEventListener(name, event => {
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = active ? "none" : "copy";
            if (!active) dropzone.classList.add("is-drag-over");
        });
    }
    dropzone?.addEventListener("dragleave", event => {
        if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("is-drag-over");
    });
    dropzone?.addEventListener("drop", event => {
        event.preventDefault();
        dropzone.classList.remove("is-drag-over");
        if (active) return;
        const files = event.dataTransfer?.files;
        if (!files?.length) return;
        if (files.length !== 1) {
            status.textContent = "Перетащите один файл. Остальные можно загрузить по очереди.";
            return;
        }
        const error = validateFile(files[0]);
        if (error) { status.textContent = error; return; }
        const transfer = new DataTransfer();
        transfer.items.add(files[0]);
        fileInput.files = transfer.files;
        showSelection();
    });
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
        dropzone?.classList.toggle("is-disabled", value);
        dropzone?.classList.remove("is-drag-over");
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
        const file = fileInput.files[0];
        if (!file) return;
        const error = validateFile(file);
        if (error) { status.textContent = error; return; }
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
