import { apiFetch, getApiBaseUrl, readResponse } from "./api.js?v=20261111c";

const form = document.getElementById("login-form");
const errorMessage = document.getElementById("error-message");
const emailInput = document.getElementById("email");
const params = new URLSearchParams(window.location.search);
const googleLoginBtn = document.getElementById("btn-google-login");

if (params.has("email")) {
    emailInput.value = params.get("email");
}
if (params.get("activated") === "1") {
    errorMessage.textContent = "Email đã được xác thực. Bạn có thể đăng nhập.";
    errorMessage.classList.add("success");
}

const googleErrorMessages = {
    cancelled: "Bạn đã hủy đăng nhập bằng Google.",
    invalid_response: "Yêu cầu đăng nhập Google không hợp lệ hoặc đã hết hạn.",
    token_exchange_failed: "Google không chấp nhận bước đổi mã đăng nhập. Hãy thử lại; nếu vẫn lỗi, cần kiểm tra cấu hình OAuth.",
    id_token_invalid: "Không thể xác minh ID token từ Google. Hãy thử lại; nếu vẫn lỗi, cần kiểm tra cấu hình OAuth.",
    unverified_email: "Google chưa xác minh địa chỉ email của bạn."
};
if (params.has("google_error")) {
    errorMessage.textContent =
        googleErrorMessages[params.get("google_error")] ||
        "Đăng nhập bằng Google chưa thành công. Vui lòng thử lại.";
    errorMessage.classList.add("error");
}

document.getElementById("register-button").addEventListener("click", () => {
    window.location.href = "register.html";
});

async function redirectIfLoggedIn() {
    try {
        const response = await apiFetch("/auth/me");
        if (response.ok) {
            window.location.replace("index.html");
            return;
        }

        if (response.status === 403) {
            window.location.replace("activate.html");
        }
    } catch (error) {
        console.error("Could not check the current session:", error);
    }
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorMessage.textContent = "";

    try {
        const response = await apiFetch("/auth/login", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                account_email: document.getElementById("email").value,
                password: document.getElementById("password").value
            })
        });

        const data = await readResponse(response);
        if (response.ok) {
            window.location.replace("index.html");
            return;
        }

        if (response.status === 403) {
            const email = emailInput.value.trim();
            window.location.replace(`activate.html?email=${encodeURIComponent(email)}`);
            return;
        }

        errorMessage.textContent =
            data.detail || `Server error (${response.status})`;
        errorMessage.classList.toggle("error", true);
    } catch (error) {
        console.error(error);
        errorMessage.textContent = "Không thể kết nối đến server";
    }
});

document.getElementById("login-form").addEventListener("input", () => {
    errorMessage.textContent = "";
    errorMessage.classList.remove("error", "success");
});

if (googleLoginBtn) {
    googleLoginBtn.addEventListener("click", async () => {
        googleLoginBtn.disabled = true;
        errorMessage.textContent = "";
        errorMessage.classList.remove("error", "success");

        try {
            const apiBaseUrl = await getApiBaseUrl();
            window.location.assign(`${apiBaseUrl}/auth/google`);
        } catch (error) {
            console.error("Could not start Google sign-in:", error);
            errorMessage.textContent =
                "Không thể kết nối đến server để đăng nhập bằng Google.";
            errorMessage.classList.add("error");
            googleLoginBtn.disabled = false;
        }
    });
}

redirectIfLoggedIn();