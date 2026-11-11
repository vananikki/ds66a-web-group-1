import { apiFetch, requireAccountAccess } from "./api.js?v=20261029a";

const pageMessage = document.getElementById("page-message");
const logoutButton = document.getElementById("logout-button");

async function checkSuperAdminAndLoadData() {
    try {
        // 1. Kiểm tra session và bắt buộc quyền SUPER_ADMIN (hoặc ADMIN)
        const account = await requireAccountAccess({
            allowedRoles: ["SUPER_ADMIN", "ADMIN"],
            redirectTo: "login.html",
        });

        if (!account) return;

        // Nếu là role thường USER mà vào thì chặn
        if (account.role !== "SUPER_ADMIN" && account.role !== "ADMIN") {
            alert("Bạn không có quyền truy cập trang Quản trị viên tối cao!");
            window.location.replace("index.html");
            return;
        }

        // 2. Điền số liệu mẫu UI (Preview Mode)
        document.getElementById("stat-total-users").textContent = "128";
        document.getElementById("stat-active-contests").textContent = "3";
        document.getElementById("stat-submissions-today").textContent = "452";
    } catch (error) {
        console.warn("Chưa bật backend auth check hoặc đang dùng chế độ Preview UI:", error);
        // Fallback số liệu mẫu preview nếu chưa bật API
        document.getElementById("stat-total-users").textContent = "128";
        document.getElementById("stat-active-contests").textContent = "3";
        document.getElementById("stat-submissions-today").textContent = "452";
    }
}

// Xử lý sự kiện Đăng xuất
if (logoutButton) {
    logoutButton.addEventListener("click", async () => {
        try {
            const response = await apiFetch("/auth/logout", { method: "POST" });
            if (!response.ok) {
                throw new Error(`Không thể đăng xuất (${response.status}).`);
            }
            window.location.replace("login.html");
        } catch (error) {
            console.error(error);
            if (pageMessage) {
                pageMessage.textContent = error instanceof Error ? error.message : "Không thể đăng xuất.";
                pageMessage.classList.add("error");
            }
        }
    });
}

checkSuperAdminAndLoadData();