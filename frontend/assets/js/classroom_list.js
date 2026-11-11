import { apiFetch, readResponse, requireAccountAccess } from "./api.js?v=20261029a";

const pageMessage = document.getElementById("page-message");
const adminHome = document.getElementById("admin-home");
const studentHome = document.getElementById("student-home");
const logoutButton = document.getElementById("logout-button");
const showCreateClassButton = document.getElementById("show-create-class");
const cancelCreateClassButton = document.getElementById("cancel-create-class");
const createClassForm = document.getElementById("create-class-form");
const createClassMessage = document.getElementById("create-class-message");
const newClassNameInput = document.getElementById("new-class-name");

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;",
    })[character]);
}

function setMessage(element, text, isError = false) {
    element.textContent = text;
    element.classList.toggle("error", isError);
    element.classList.toggle("success", !isError && Boolean(text));
}

async function requestJson(path, options) {
    const response = await apiFetch(path, options);
    const data = await readResponse(response);
    if (!response.ok) {
        throw new Error(data.detail || `Yêu cầu thất bại (${response.status})`);
    }
    return data;
}

function classCard(item, { joined = false, managed = false } = {}) {
    const listView = managed ? "managed-classes" : "my-classes";
    const href = `classroom.html?class_id=${encodeURIComponent(item.class_id)}&from=${listView}`;
    const canOpenClass = (managed && !item.is_claimable) ||
        joined ||
        item.membership_status === "joined";
    const openClassLink = `<a class="button-link class-card-open" href="${href}">Mở lớp học <span aria-hidden="true">→</span></a>`;
    let actions;
    if (managed) {
        actions = item.is_claimable
            ? `<button type="button" data-action="claim-class" data-class-id="${item.class_id}">Nhận quyền quản lý</button>`
            : openClassLink;
    } else if (joined || item.membership_status === "joined") {
        actions = openClassLink;
    } else if (item.membership_status === "pending") {
        actions = `<span class="status-pill pending">Đang chờ duyệt</span>`;
    } else if (item.membership_status === "rejected") {
        actions = `<button type="button" class="secondary" data-action="request-join" data-class-id="${item.class_id}">Gửi lại yêu cầu</button>`;
    } else {
        actions = `<button type="button" data-action="request-join" data-class-id="${item.class_id}">Xin tham gia</button>`;
    }

    return `<article class="class-card">
        <div class="class-card-icon" aria-hidden="true">▤</div>
        <div class="class-card-main">
            <p class="class-card-kicker">LỚP HỌC ${item.class_id ? `· #${item.class_id}` : ""}</p>
            ${canOpenClass
                ? `<a class="class-card-title-link" href="${href}"><h3>${escapeHtml(item.class_name)}</h3></a>`
                : `<h3>${escapeHtml(item.class_name)}</h3>`}
            <div class="class-card-actions">${actions}</div>
        </div>
    </article>`;
}

function renderCards(element, items, options = {}) {
    const list = Array.isArray(items) ? items : [];
    if (!list.length) {
        element.innerHTML = `<div class="empty-state"><span aria-hidden="true">✦</span><p>${options.emptyText || "Chưa có lớp học."}</p></div>`;
        return;
    }
    element.innerHTML = list.map((item) => classCard(item, options)).join("");
}

function renderListNotice(element, text, isError = false) {
    const notice = document.createElement("div");
    notice.className = "empty-state";
    if (isError) {
        notice.classList.add("error");
    }
    const message = document.createElement("p");
    message.textContent = text;
    notice.append(message);
    element.replaceChildren(notice);
}

function setActiveSidebarItem(activeItem) {
    document.querySelectorAll(".nav-item").forEach((navItem) => {
        navItem.classList.toggle("active", navItem === activeItem);
    });
}

async function loadAdminClasses() {
    const list = document.getElementById("managed-class-list");
    renderListNotice(list, "Đang tải danh sách lớp...");
    try {
        const classes = await requestJson("/admin/judge/academic-classes");
        renderCards(list, classes, {
            managed: true,
            emptyText: "Chưa có lớp nào. Tạo lớp đầu tiên để bắt đầu.",
        });
    } catch (error) {
        renderListNotice(list, error.message || "Không thể tải danh sách lớp.", true);
        throw error;
    }
}

async function loadStudentClasses() {
    const joinedList = document.getElementById("my-class-list");
    const availableList = document.getElementById("available-class-list");
    renderListNotice(joinedList, "Đang tải lớp đã tham gia...");
    renderListNotice(availableList, "Đang tải danh sách lớp...");

    const [joinedResult, availableResult] = await Promise.allSettled([
        requestJson("/student/classes"),
        requestJson("/student/available-classes"),
    ]);

    if (joinedResult.status === "fulfilled") {
        renderCards(joinedList, joinedResult.value, {
            joined: true,
            emptyText: "Bạn chưa tham gia lớp nào.",
        });
    } else {
        renderListNotice(
            joinedList,
            joinedResult.reason.message || "Không thể tải lớp đã tham gia.",
            true,
        );
    }

    if (availableResult.status === "fulfilled") {
        renderCards(availableList, availableResult.value, {
            emptyText: "Hiện chưa có lớp nào để tham gia.",
        });
    } else {
        renderListNotice(
            availableList,
            availableResult.reason.message || "Không thể tải danh sách lớp.",
            true,
        );
    }
    if (joinedResult.status === "rejected" || availableResult.status === "rejected") {
        throw joinedResult.status === "rejected"
            ? joinedResult.reason
            : availableResult.reason;
    }
}

async function initialize() {
    try {
        const currentAccount = await requireAccountAccess({
            allowedRoles: ["USER", "ADMIN", "SUPER_ADMIN"],
            redirectTo: "login.html",
        });
        if (!currentAccount) {
            return;
        }
        const isStudent = currentAccount.role === "USER";
        const view = new URLSearchParams(window.location.search).get("view");
        const forceStudentView = view === "my-classes";
        const adminHomeNav = document.getElementById("admin-home-nav");
        const studentHomeNav = document.getElementById("student-home-nav");
        const sidebarTitle = document.getElementById("sidebar-title");
        if (adminHomeNav) {
            adminHomeNav.hidden = isStudent;
        }
        if (studentHomeNav) {
            studentHomeNav.hidden = false;
        }
        const showStudentClasses = isStudent || forceStudentView;
        setActiveSidebarItem(showStudentClasses ? studentHomeNav : adminHomeNav);
        if (sidebarTitle) {
            sidebarTitle.textContent = forceStudentView
                ? "Lớp của tôi"
                : isStudent
                    ? "Lớp học"
                    : "Quản lý lớp";
        }
        if (showStudentClasses) {
            document.getElementById("page-heading").textContent = "Lớp học của tôi";
            document.getElementById("page-subtitle").textContent =
                "Xin tham gia lớp học để mở bài tập và cuộc thi được giao.";
            adminHome.hidden = true;
            studentHome.hidden = false;
            await loadStudentClasses();
        } else {
            document.getElementById("advanced-admin-link").hidden = false;
            document.getElementById("page-heading").textContent = "Không gian quản lý lớp";
            document.getElementById("page-subtitle").textContent =
                "Tổ chức lớp học, quản lý quyền truy cập và chuẩn bị bài tập cho học viên.";
            adminHome.hidden = false;
            studentHome.hidden = true;
            await loadAdminClasses();
        }
    } catch (error) {
        console.error(error);
        setMessage(pageMessage, error.message || "Không thể tải lớp học.", true);
    }
}

function bindActionButtons() {
    document.body.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-action]");
        if (!button) return;
        button.disabled = true;
        try {
            const classId = Number(button.dataset.classId);
            switch (button.dataset.action) {
                case "claim-class":
                    await requestJson(`/admin/judge/academic-classes/${classId}/claim`, { method: "POST" });
                    setMessage(pageMessage, "Bạn đã trở thành chủ lớp.");
                    await loadAdminClasses();
                    break;
                case "request-join":
                    await requestJson(`/student/classes/${classId}/join-requests`, { method: "POST" });
                    setMessage(pageMessage, "Đã gửi yêu cầu. Bạn có thể vào lớp sau khi chủ lớp duyệt.");
                    await loadStudentClasses();
                    break;
            }
        } catch (error) {
            setMessage(pageMessage, error.message || "Thao tác không thành công.", true);
        } finally {
            button.disabled = false;
        }
    });
}

if (showCreateClassButton && createClassForm && newClassNameInput) {
    showCreateClassButton.addEventListener("click", () => {
        createClassForm.hidden = false;
        newClassNameInput.focus();
    });
}

if (cancelCreateClassButton && createClassForm) {
    cancelCreateClassButton.addEventListener("click", () => {
        createClassForm.reset();
        createClassForm.hidden = true;
    });
}

if (createClassForm) {
    createClassForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const button = form.querySelector('[type="submit"]');
        const className = new FormData(form).get("class_name")?.toString().trim();

        if (!className) {
            setMessage(createClassMessage, "Vui lòng nhập tên lớp học.", true);
            return;
        }

        button.disabled = true;
        try {
            // Dùng trực tiếp apiFetch thay vì requestJson để tự kiểm soát response
            const response = await apiFetch("/admin/judge/academic-classes", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ class_name: className }),
            });

            // Kiểm tra response.ok (chấp nhận cả 200 OK và 201 Created)
            if (response.ok) {
                form.reset();
                form.hidden = true;
                setMessage(createClassMessage, "Đã tạo lớp mới. Bạn đang là chủ lớp.", false);
                await loadAdminClasses();
            } else {
                // Đọc an toàn thông báo lỗi từ server nếu status >= 400
                let errorMsg = `Yêu cầu thất bại (${response.status})`;
                try {
                    const errorData = await readResponse(response);
                    if (errorData && errorData.detail) {
                        errorMsg = errorData.detail;
                    }
                } catch {
                    const fallbackText = await response.text();
                    if (fallbackText) errorMsg = fallbackText;
                }
                setMessage(createClassMessage, errorMsg, true);
            }
        } catch (networkError) {
            console.error("Lỗi gửi yêu cầu tạo lớp:", networkError);
            setMessage(createClassMessage, "Không thể kết nối đến máy chủ. Vui lòng thử lại.", true);
        } finally {
            button.disabled = false;
        }
    });
}

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
            setMessage(pageMessage, error instanceof Error ? error.message : "Không thể đăng xuất.", true);
        }
    });
}

bindActionButtons();
initialize();
