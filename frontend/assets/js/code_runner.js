import { apiFetch, readResponse } from "./api.js?v=20261029a";

export function attachCodeRunner({
    button,
    input,
    status,
    stdout,
    errors,
    languageSelect,
    getSourceCode,
    getRequest,
    isAllowed = () => true,
}) {
    button.addEventListener("click", async () => {
        if (!isAllowed()) return;
        const sourceCode = getSourceCode();
        if (!sourceCode.trim()) {
            status.textContent = "Vui lòng nhập mã nguồn trước khi chạy.";
            status.classList.add("error");
            status.classList.remove("success");
            return;
        }

        button.disabled = true;
        status.textContent = "Đang chạy trên Judge0...";
        status.classList.remove("error", "success");
        stdout.textContent = "";
        errors.textContent = "";
        errors.hidden = true;
        try {
            const { path, extraPayload = {} } = getRequest();
            const response = await apiFetch(path, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    language_id: Number(languageSelect.value),
                    source_code: sourceCode,
                    stdin: input.value,
                    ...extraPayload,
                }),
            });
            const result = await readResponse(response);
            if (!response.ok) {
                if (response.status === 404 && result.detail === "Not Found") {
                    throw new Error(
                        "Backend chưa có API chạy thử mới. Hãy khởi động lại backend rồi thử lại.",
                    );
                }
                throw new Error(result.detail || `Không thể chạy code (${response.status})`);
            }

            const successful = result.status_id === 3;
            const metrics = [
                result.runtime_ms == null ? null : `${result.runtime_ms} ms`,
                result.memory_kb == null ? null : `${result.memory_kb} KB`,
            ].filter(Boolean);
            const runStatus = successful ? "Chạy xong" : result.status;
            status.textContent = `${runStatus}${metrics.length ? ` · ${metrics.join(" · ")}` : ""}`;
            status.classList.toggle("success", successful);
            status.classList.toggle("error", !successful);
            stdout.textContent = result.stdout || "(Chương trình không in ra stdout)";
            const errorOutput = [result.compile_output, result.stderr]
                .filter(Boolean)
                .join("\n");
            errors.textContent = errorOutput;
            errors.hidden = !errorOutput;
        } catch (error) {
            status.textContent = error instanceof Error ? error.message : "Không thể chạy code.";
            status.classList.add("error");
            status.classList.remove("success");
        } finally {
            button.disabled = !isAllowed();
        }
    });
}
