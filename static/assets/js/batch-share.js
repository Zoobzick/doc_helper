(() => {
    "use strict";
    document.querySelectorAll(".js-scan-share-form").forEach(form => {
        let busy = false;
        form.addEventListener("submit", async event => {
            event.preventDefault();
            if (busy) return;
            busy = true;
            const button = form.querySelector("button");
            const status = form.querySelector(".js-share-status");
            const link = form.querySelector(".js-share-url");
            button.disabled = true;
            link.hidden = true;
            status.textContent = "Создаём ссылку…";
            try {
                const response = await fetch(form.action, {
                    method: "POST", credentials: "same-origin",
                    headers: {"X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value},
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.url) throw new Error(data.error || "Не удалось создать ссылку. Проверьте подключение и вход в приложение.");
                link.value = data.url;
                try {
                    await navigator.clipboard.writeText(data.url);
                    status.textContent = "Ссылка скопирована · доступ 7 дней";
                } catch (_) {
                    link.hidden = false;
                    link.focus();
                    link.select();
                    status.textContent = "Ссылка готова на 7 дней. Скопируйте её из поля ниже.";
                }
            } catch (error) {
                status.textContent = error.message;
            } finally {
                busy = false;
                button.disabled = false;
            }
        });
    });
})();
