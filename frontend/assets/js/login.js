import { apiFetch, readResponse } from "./api.js?v=20261029a";

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
    googleLoginBtn.addEventListener("click", () => {
        console.log("Trigger Google Login flow");
        alert("Đã kết nối UI: Chờ Backend tích hợp OAuth2 Client ID!");
    });
}

redirectIfLoggedIn();