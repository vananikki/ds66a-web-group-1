import { apiFetch, readResponse } from "./api.js?v=20261029a";

const form = document.getElementById("register-form");
const statusMessage = document.getElementById("status-message");
const openTerms = document.getElementById("open-terms-link");
const closeTerms = document.getElementById("close-terms-btn");
const modal = document.getElementById("terms-modal");
const termsCheckbox = document.getElementById("terms-agree");

document.getElementById("login-button").addEventListener("click", () => {
    window.location.href = "login.html";
});

// Xử lý mở / đóng Modal Điều khoản
if (openTerms && modal) {
    openTerms.addEventListener("click", () => {
        modal.style.display = "flex";
    });
}

if (closeTerms && modal) {
    closeTerms.addEventListener("click", () => {
        modal.style.display = "none";
    });
}

if (modal) {
    modal.addEventListener("click", (event) => {
        if (event.target === modal) {
            modal.style.display = "none";
        }
    });
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    statusMessage.textContent = "";

    // Kiểm tra checkbox điều khoản
    if (termsCheckbox && !termsCheckbox.checked) {
        alert("Vui lòng đọc và tích đồng ý với Điều khoản sử dụng trước khi tạo tài khoản!");
        termsCheckbox.focus();
        return;
    }

    const accountName = document.getElementById("account-name").value.trim();
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    const confirmPassword = document.getElementById("confirm-password").value;

    if (password !== confirmPassword) {
        statusMessage.textContent = "Mật khẩu nhập lại không khớp";
        return;
    }

    const passwordRequirements = /^(?=.*[A-Z])(?=.*[a-zA-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$/;
    if (!passwordRequirements.test(password)) {
        statusMessage.textContent = "Mật khẩu phải có ít nhất 8 ký tự, có chữ và số, 1 ký tự đặc biệt và 1 chữ cái in hoa.";
        return;
    }

    try {
        const response = await apiFetch("/auth/register", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                account_name: accountName,
                account_email: email,
                password
            })
        });
        const data = await readResponse(response);

        if (!response.ok) {
            statusMessage.textContent =
                data.detail || `Không thể tạo tài khoản (${response.status})`;
            return;
        }

        statusMessage.textContent =
            "Tạo tài khoản thành công. Đang chuyển đến đăng nhập...";
        window.setTimeout(() => {
            window.location.replace("login.html");
        }, 1000);
    } catch (error) {
        console.error(error);
        statusMessage.textContent = "Không thể kết nối đến server";
    }
});