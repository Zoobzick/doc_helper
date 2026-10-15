(() => {
    "use strict";
    document.querySelectorAll(".js-scan-share-form").forEach(form => {
        let busy = false;
        form.addEventListener("submit", async event => {
            event.preventDefault();
            if (busy) return;
            busy = true;
            const button = form.querySelector("button");
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            try {
                const response = await fetch(form.action, {
                    method: "POST", credentials: "same-origin",
                    headers: {"X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value},
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.url) {
                    window.showDocToast(data.error || "Не удалось создать ссылку. Проверьте подключение и вход в приложение.", response.status === 400 ? "warning" : "error");
                    return;
                }
                try {
                    await navigator.clipboard.writeText(data.url);
                    window.showDocToast("Ссылка на сканы ИД скопирована. Доступ открыт на 7 дней.", "success");
                } catch (_) {
                    window.showDocToast("Браузер не разрешил копирование. Скопируйте ссылку из поля в этом уведомлении. Доступ — 7 дней.", "warning", {copyValue: data.url});
                }
            } catch (error) {
                window.showDocToast(error.message, "error");
            } finally {
                busy = false;
                button.disabled = false;
                button.removeAttribute("aria-busy");
            }
        });
    });
})();
