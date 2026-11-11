import { apiFetch, readResponse, requireAccountAccess } from "./api.js?v=20261029a";
import { typesetMath } from "./latex.js?v=20261101b";
import { renderSubmissionHistory } from "./submission_history.js?v=20261102a";

const pageMessage = document.getElementById("page-message");
const adminHome = document.getElementById("admin-home");
const studentHome = document.getElementById("student-home");
const workspace = document.getElementById("class-workspace");
const createClassForm = document.getElementById("create-class-form");
const showCreateClassButton = document.getElementById("show-create-class");
const cancelCreateClassButton = document.getElementById("cancel-create-class");
let currentAccount;
let activeClassId;
let managedClassId;
let canCreateClassContest = false;
let classListView = "my-classes";
let contestProblemCatalog = [];
let selectedContestProblemIds = [];
const maxContestProblems = 20;
const submissionHistoryPageSize = 50;
let submissionHistoryOffset = 0;
let managerSearchTimer;
let managerSearchRequest = 0;
let memberSearchTimer;
let memberSearchRequest = 0;

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;",
    })[character]);
}

function truncateText(value, maxLength = 120) {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    if (!text) {
        return "";
    }
    if (text.length <= maxLength) {
        return text;
    }
    return `${text.slice(0, maxLength).trimEnd()}…`;
}

function setMessage(element, text, isError = false) {
    element.textContent = text;
    element.classList.toggle("error", isError);
    element.classList.toggle("success", !isError && Boolean(text));
}

function renderContestProblemSelection() {
    const picker = document.getElementById("contest-problem-picker");
    const addButton = document.getElementById("add-contest-problem");
    const selectedList = document.getElementById("contest-selected-problems");
    const selectionStatus = document.getElementById("contest-problem-selection-status");
    const submitButton = document.getElementById("create-contest-button");
    if (!picker || !addButton || !selectedList || !selectionStatus || !submitButton) {
        return;
    }

    const selectedIds = new Set(selectedContestProblemIds);
    const remaining = contestProblemCatalog.filter((problem) => !selectedIds.has(problem.problem_id));
    picker.innerHTML = remaining.length
        ? `<option value="">Chọn bài để thêm...</option>${remaining.map((problem) =>
            `<option value="${problem.problem_id}">${escapeHtml(problem.problem_name)}</option>`
        ).join("")}`
        : `<option value="">${selectedContestProblemIds.length >= maxContestProblems
            ? "Đã đạt giới hạn 20 bài"
            : "Đã thêm hết bài trong lớp"}</option>`;
    picker.value = "";
    addButton.disabled = remaining.length === 0 || selectedContestProblemIds.length >= maxContestProblems;

    selectedList.innerHTML = selectedContestProblemIds.map((problemId, index) => {
        const problem = contestProblemCatalog.find((item) => item.problem_id === problemId);
        if (!problem) {
            return "";
        }
        return `<li class="contest-selected-problem">
            <span class="contest-problem-order">Bài ${index + 1}</span>
            <strong class="contest-selected-problem-name">${escapeHtml(problem.problem_name)}</strong>
            <span class="contest-selected-problem-actions">
                <button class="secondary" type="button" data-contest-action="up" data-index="${index}" aria-label="Đưa ${escapeHtml(problem.problem_name)} lên trước" ${index === 0 ? "disabled" : ""}>↑</button>
                <button class="secondary" type="button" data-contest-action="down" data-index="${index}" aria-label="Đưa ${escapeHtml(problem.problem_name)} xuống sau" ${index === selectedContestProblemIds.length - 1 ? "disabled" : ""}>↓</button>
                <button class="secondary" type="button" data-contest-action="remove" data-index="${index}" aria-label="Bỏ ${escapeHtml(problem.problem_name)} khỏi cuộc thi">×</button>
            </span>
        </li>`;
    }).join("");
    selectionStatus.textContent = selectedContestProblemIds.length
        ? `${selectedContestProblemIds.length}/${maxContestProblems} bài đã chọn. Thứ tự bên trên sẽ là số bài trong contest.`
        : "Chưa chọn bài tập nào.";
    submitButton.disabled = selectedContestProblemIds.length === 0;
}

function formatDateTimeLocal(value) {
    const date = new Date(value);
    date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
    return date.toISOString().slice(0, 16);
}

function resetContestForm() {
    const form = document.getElementById("create-contest-form");
    form.reset();
    delete form.dataset.contestId;
    form.hidden = !canCreateClassContest;
    selectedContestProblemIds = [];
    document.getElementById("contest-form-heading").textContent =
        canCreateClassContest ? "Tạo cuộc thi" : "Quản lý cuộc thi";
    document.getElementById("create-contest-button").textContent = "Tạo cuộc thi";
    document.getElementById("cancel-contest-edit").hidden = true;
    setMessage(document.getElementById("create-contest-message"), "");
    renderContestProblemSelection();
}

function editContest(contest) {
    const form = document.getElementById("create-contest-form");
    form.hidden = false;
    form.dataset.contestId = contest.contest_id;
    form.elements.contest_name.value = contest.contest_name;
    form.elements.description.value = contest.description || "";
    form.elements.start_time.value = formatDateTimeLocal(contest.start_time);
    form.elements.end_time.value = formatDateTimeLocal(contest.end_time);
    selectedContestProblemIds = [...contest.problem_ids];
    document.getElementById("contest-form-heading").textContent =
        `Sửa cuộc thi: ${contest.contest_name}`;
    document.getElementById("create-contest-button").textContent = "Lưu thay đổi";
    document.getElementById("cancel-contest-edit").hidden = false;
    setMessage(
        document.getElementById("create-contest-message"),
        "Có thể sửa cả thời gian và danh sách bài, kể cả khi contest đã có lịch sử nộp.",
    );
    renderContestProblemSelection();
    document.getElementById("create-contest-form").scrollIntoView({
        behavior: "smooth",
        block: "center",
    });
}

function renderProblemMath(element) {
    typesetMath(element).catch((error) => {
        console.error(error);
        setMessage(pageMessage, error.message || "Không thể hiển thị công thức LaTeX.", true);
    });
}

async function requestJson(path, options) {
    const response = await apiFetch(path, options);
    const data = await readResponse(response);
    if (!response.ok) {
        const error = new Error(data.detail || `Yêu cầu thất bại (${response.status})`);
        error.status = response.status;
        throw error;
    }
    return data;
}

function classCard(item, { joined = false, managed = false } = {}) {
    const actions = managed
        ? item.is_claimable
            ? `<button type="button" data-action="claim-class" data-class-id="${item.class_id}">Nhận quyền quản lý</button>`
            : `<button type="button" data-action="open-admin-class" data-class-id="${item.class_id}">Vào lớp</button>`
        : joined
            ? `<button type="button" data-action="open-student-class" data-class-id="${item.class_id}">Vào lớp</button>`
            : item.membership_status === "pending"
                ? `<span class="status-pill pending">Đang chờ duyệt</span>`
                : item.membership_status === "rejected"
                    ? `<button type="button" class="secondary" data-action="request-join" data-class-id="${item.class_id}">Gửi lại yêu cầu</button>`
                    : item.membership_status === "joined"
                        ? `<span class="status-pill">Đã tham gia</span>`
                        : `<button type="button" data-action="request-join" data-class-id="${item.class_id}">Xin tham gia</button>`;
    return `<article class="class-card">
        <div class="class-card-icon" aria-hidden="true">▤</div>
        <div class="class-card-main">
            <p class="class-card-kicker">LỚP HỌC ${item.class_id ? `· #${item.class_id}` : ""}</p>
            <h3>${escapeHtml(item.class_name)}</h3>
            <div class="class-card-actions">${actions}</div>
        </div>
    </article>`;
}

function renderCards(element, items, options = {}) {
    if (!items.length) {
        element.innerHTML = `<div class="empty-state"><span aria-hidden="true">✦</span><p>${escapeHtml(options.emptyText || "Chưa có lớp học.")}</p></div>`;
        return;
    }
    element.innerHTML = items.map((item) => classCard(item, options)).join("");
}

function activateClassTab(name, focus = false) {
    const tabs = [...document.querySelectorAll("#class-tabs [role='tab']")];
    const availableTabs = tabs.filter((tab) => !tab.hidden);
    const activeTab = availableTabs.find((tab) => tab.dataset.classTab === name) || availableTabs[0];
    if (!activeTab) {
        return;
    }

    tabs.forEach((tab) => {
        const isActive = tab === activeTab;
        tab.classList.toggle("active", isActive);
        tab.setAttribute("aria-selected", String(isActive));
        tab.tabIndex = isActive ? 0 : -1;
    });

    document.querySelectorAll("[data-class-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.classPanel !== activeTab.dataset.classTab;
    });
    if (focus) {
        activeTab.focus();
    }
}

document.getElementById("class-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest("[role='tab']");
    if (tab && !tab.hidden) {
        activateClassTab(tab.dataset.classTab);
    }
});

document.getElementById("class-tabs").addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll("#class-tabs [role='tab']:not([hidden])")];
    const currentIndex = tabs.indexOf(event.target);
    if (currentIndex < 0) {
        return;
    }

    let nextIndex;
    if (event.key === "ArrowRight") {
        nextIndex = (currentIndex + 1) % tabs.length;
    } else if (event.key === "ArrowLeft") {
        nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    } else if (event.key === "Home") {
        nextIndex = 0;
    } else if (event.key === "End") {
        nextIndex = tabs.length - 1;
    } else {
        return;
    }

    event.preventDefault();
    activateClassTab(tabs[nextIndex].dataset.classTab, true);
});

async function loadAdminClasses() {
    const classes = await requestJson("/admin/judge/academic-classes");
    renderCards(document.getElementById("managed-class-list"), classes, {
        managed: true,
        emptyText: "Chưa có lớp nào. Tạo lớp đầu tiên để bắt đầu.",
    });
}

async function loadStudentClasses() {
    const [joined, available] = await Promise.all([
        requestJson("/student/classes"),
        requestJson("/student/available-classes"),
    ]);
    renderCards(document.getElementById("my-class-list"), joined, {
        joined: true,
        emptyText: "Bạn chưa tham gia lớp nào.",
    });
    renderCards(document.getElementById("available-class-list"), available, {
        emptyText: "Hiện chưa có lớp nào để tham gia.",
    });
}

function showHome() {
    if (workspace) {
        workspace.hidden = true;
    }
    if (adminHome) {
        adminHome.hidden = currentAccount.role === "USER";
    }
    if (studentHome) {
        studentHome.hidden = currentAccount.role !== "USER";
    }
    window.location.href = "classroom_list.html?view=my-classes";
}

async function openClass(classId, isStudent) {
    if (document.getElementById("create-contest-form").dataset.contestId) {
        resetContestForm();
    }
    activeClassId = Number(classId);
    clearTimeout(managerSearchTimer);
    managerSearchRequest += 1;
    clearTimeout(memberSearchTimer);
    memberSearchRequest += 1;
    pageMessage.textContent = "";

    let data;
    let resolvedAsStudent = isStudent;
    try {
        data = await requestJson(
            resolvedAsStudent
                ? `/student/classes/${activeClassId}`
                : `/admin/judge/academic-classes/${activeClassId}`,
        );
    } catch (error) {
        if (!resolvedAsStudent && (error.status === 403 || error.status === 404)) {
            resolvedAsStudent = true;
            try {
                data = await requestJson(`/student/classes/${activeClassId}`);
            } catch (fallbackError) {
                if (fallbackError.status === 403 || fallbackError.status === 404) {
                    setMessage(pageMessage, fallbackError.message || "Bạn chưa tham gia lớp này.", true);
                    return;
                }
                throw fallbackError;
            }
        } else if (error.status === 403 || error.status === 404) {
            setMessage(pageMessage, error.message || "Bạn không có quyền truy cập lớp này.", true);
            return;
        } else {
            throw error;
        }
    }
    if (adminHome) {
        adminHome.hidden = true;
    }
    if (studentHome) {
        studentHome.hidden = true;
    }
    workspace.hidden = false;
    document.getElementById("workspace-title").textContent = data.class_name;
    document.getElementById("workspace-meta").textContent =
        `Lớp #${data.class_id} · ${(data.problems || []).length} bài tập · ${(data.contests || []).length} cuộc thi`;

    const canManage = !resolvedAsStudent;
    if (canManage) {
        managedClassId = activeClassId;
    }
    const canManagePermissions = canManage && (
        currentAccount.role === "SUPER_ADMIN" || data.is_owner
    );
    const canReviewJoinRequests = canManage && data.is_owner;
    canCreateClassContest = canReviewJoinRequests;
    document.querySelectorAll(".admin-class-tools").forEach((tool) => {
        tool.hidden = !canManage;
    });
    document.getElementById("create-contest-tools").hidden = !canManage;
    document.getElementById("create-contest-form").hidden = !canCreateClassContest;
    document.getElementById("join-request-tools").hidden = !canReviewJoinRequests;
    document.getElementById("manager-tools").hidden = !canManagePermissions;
    document.getElementById("class-tab-history").hidden = canManage;
    activateClassTab(
        document.querySelector("#class-tabs [aria-selected='true']")?.dataset.classTab || "problems",
    );
    const members = data.students || [];
    const isCurrentAccountMember = members.some(
        (member) => member.account_id === currentAccount.account_id,
    );
    const studentModeSwitch = document.getElementById("student-mode-switch");
    const managementModeSwitch = document.getElementById("management-mode-switch");
    studentModeSwitch.hidden = !(
        canManage &&
        isCurrentAccountMember &&
        currentAccount.role !== "USER"
    );
    managementModeSwitch.hidden = !(
        resolvedAsStudent &&
        managedClassId === activeClassId &&
        currentAccount.role !== "USER"
    );
    const ownerId = data.owner_account_id ?? null;
    const studentMembers = members.filter((member) => {
        const isOwner = ownerId !== null && member.account_id === ownerId;
        return !isOwner && (member.account_role || "USER") === "USER";
    });
    document.getElementById("member-count-badge").textContent = String(studentMembers.length);
    document.getElementById("member-list").innerHTML = members.map((member) => {
        const isOwner = ownerId !== null && member.account_id === ownerId;
        const roleValue = isOwner
            ? "Chủ lớp"
            : member.account_role === "SUPER_ADMIN"
                ? "Quản trị cấp cao"
                : member.account_role === "ADMIN"
                    ? "Quản trị viên"
                    : "Học viên";
        const roleClass = isOwner
            ? "owner"
            : member.account_role === "USER"
                ? "student"
                : "manager";
        const canRemove = canManagePermissions && !isOwner;
        return `
            <div class="member-item">
                <div class="member-item-main">
                    <strong>${escapeHtml(member.account_name)}</strong>
                    ${member.account_email ? `<small>${escapeHtml(member.account_email)}</small>` : ""}
                </div>
                <div class="member-item-meta">
                    <span class="member-role ${roleClass}">${roleValue}</span>
                    ${canRemove ? `<button class="secondary" type="button" data-action="remove-member" data-account-id="${member.account_id}">Gỡ khỏi lớp</button>` : ""}
                </div>
            </div>`;
    }).join("") || `<p class="field-hint">Chưa có thành viên.</p>`;
    const memberInviteTools = document.getElementById("member-invite-tools");
    if (memberInviteTools) {
        memberInviteTools.hidden = !canManagePermissions;
    }

    document.getElementById("class-problem-list").innerHTML = (data.problems || []).map((problem) => {
        const shortDescription = truncateText(problem.description || "", 120);
        return `
            <a class="problem-card problem-card-link" href="problem_submit.html?problem_id=${encodeURIComponent(problem.problem_id)}&source=class&class_id=${encodeURIComponent(activeClassId)}&from=${encodeURIComponent(classListView)}">
                <div>
                    <p class="class-card-kicker">BÀI TẬP · #${escapeHtml(problem.problem_id)}</p>
                    <h4>${escapeHtml(problem.problem_name)}</h4>
                    ${problem.description?.trim() ? `<p>${escapeHtml(shortDescription)}</p>` : ""}
                    ${problem.is_required ? `<span class="status-pill">Bắt buộc</span>` : ""}
                </div>
                <span class="problem-card-open-hint">${resolvedAsStudent ? "Xem đề và nộp →" : "Xem đề →"}</span>
            </a>`;
    }).join("") || `<div class="empty-state"><p>Lớp chưa có bài tập.</p></div>`;
    renderProblemMath(document.getElementById("class-problem-list"));
    document.getElementById("class-contest-list").innerHTML = (data.contests || []).map((contest) => {
        const isActive = contest.status === "active";
        const statusLabel = isActive ? "ĐANG DIỄN RA" : contest.status === "upcoming" ? "SẮP DIỄN RA" : "ĐÃ KẾT THÚC";
        const action = resolvedAsStudent
            ? contest.status !== "upcoming"
                ? `<a class="button-link" href="contest.html?class_id=${encodeURIComponent(activeClassId)}&contest_id=${encodeURIComponent(contest.contest_id)}&from=${encodeURIComponent(classListView)}">${isActive ? "Vào cuộc thi" : "Xem kết quả"}</a>`
                : `<button type="button" disabled>${statusLabel}</button>`
            : canManage
                ? `<a class="button-link secondary" href="contest.html?class_id=${encodeURIComponent(activeClassId)}&contest_id=${encodeURIComponent(contest.contest_id)}&from=${encodeURIComponent(classListView)}">Xem cuộc thi</a>
                   <button class="secondary" type="button" data-action="edit-contest" data-contest-id="${contest.contest_id}">Sửa</button>`
                : "";
        return `
            <article class="problem-card">
                <div><p class="class-card-kicker">CUỘC THI · ${statusLabel}</p><h4>${escapeHtml(contest.contest_name)}</h4></div>
                <span>${new Date(contest.start_time).toLocaleString()} – ${new Date(contest.end_time).toLocaleString()}</span>
                ${action}
            </article>`;
    }).join("") || `<div class="empty-state"><p>Chưa có cuộc thi nào.</p></div>`;
    renderProblemMath(document.getElementById("class-contest-list"));

    if (canManage) {
        await loadAttachableProblems(data.problems || []);
        contestProblemCatalog = data.problems || [];
        selectedContestProblemIds = [];
        renderContestProblemSelection();
        document.getElementById("create-contest-message").textContent =
            canCreateClassContest && !data.problems?.length
                ? "Thêm ít nhất một bài tập vào lớp trước khi tạo cuộc thi."
                : "";
        if (canReviewJoinRequests) {
            await loadJoinRequests(activeClassId);
        }
        await loadManagers(activeClassId, canManagePermissions);
        await loadAvailableMembers(activeClassId, canManagePermissions);
    } else {
        await loadSubmissionHistory(activeClassId);
    }
}

async function loadAttachableProblems(classProblems) {
    const picker = document.getElementById("class-problem-picker");
    const button = document.getElementById("add-problem-button");
    const currentClassId = activeClassId;
    setMessage(document.getElementById("add-problem-message"), "");
    picker.innerHTML = `<option value="">Đang tải bài tập...</option>`;
    button.disabled = true;
    try {
        const library = await requestJson("/admin/judge/problems");
        if (currentClassId !== activeClassId) {
            return;
        }
        const attachedIds = new Set(classProblems.map((problem) => problem.problem_id));
        const candidates = library.filter((problem) => !attachedIds.has(problem.problem_id));
        picker.innerHTML = `<option value="">Chọn bài tập để thêm</option>${candidates.map((problem) => {
            const label = problem.access_modifier === "public"
                ? "Public"
                : problem.access_modifier === "hidden"
                    ? "Hidden"
                    : "Private";
            return `<option value="${problem.problem_id}">${escapeHtml(problem.problem_name)} · ${label}</option>`;
        }).join("")}`;
        if (!candidates.length) {
            setMessage(document.getElementById("add-problem-message"), "Không còn bài trong kho của bạn hoặc bài public để thêm.");
        }
        button.disabled = !candidates.length;
    } catch (error) {
        console.error(error);
        picker.innerHTML = `<option value="">Không thể tải bài tập</option>`;
        setMessage(
            document.getElementById("add-problem-message"),
            error.message || "Không thể tải bài tập để thêm vào lớp.",
            true,
        );
    }
}

async function loadJoinRequests(classId) {
    const requests = await requestJson(`/admin/judge/academic-classes/${classId}/join-requests`);
    const list = document.getElementById("join-request-list");
    list.innerHTML = requests.map((item) => `<div class="entity-row">
        <span><strong>${escapeHtml(item.account_name)}</strong><small>${escapeHtml(item.account_email)}</small></span>
        <span class="button-row">
            <button type="button" data-action="decide-request" data-request-id="${item.join_request_id}" data-status="approved">Duyệt</button>
            <button class="secondary" type="button" data-action="decide-request" data-request-id="${item.join_request_id}" data-status="rejected">Từ chối</button>
        </span>
    </div>`).join("") || `<p class="field-hint">Không có yêu cầu nào đang chờ.</p>`;
}

async function loadManagers(classId, canManagePermissions) {
    clearTimeout(managerSearchTimer);
    const requestId = ++managerSearchRequest;
    const managers = await requestJson(`/admin/judge/academic-classes/${classId}/managers`);
    if (classId !== activeClassId || requestId !== managerSearchRequest) {
        return;
    }
    const list = document.getElementById("manager-list");
    list.innerHTML = managers.map((manager) => `<div class="entity-row">
        <span><strong>${escapeHtml(manager.account_name)}</strong><small>${escapeHtml(manager.account_email)} · ${manager.is_owner ? "Chủ lớp" : "Quản trị viên"}</small></span>
        ${canManagePermissions && (!manager.is_owner || currentAccount.role === "SUPER_ADMIN") ? `<button class="secondary" type="button" data-action="remove-manager" data-account-id="${manager.account_id}">Thu hồi quyền</button>` : ""}
    </div>`).join("");
    const search = document.getElementById("manager-search");
    const availableList = document.getElementById("available-manager-list");
    availableList.replaceChildren();
    if (!canManagePermissions) {
        search.oninput = null;
        return;
    }

    availableList.innerHTML = `<p class="field-hint">Nhập tên hoặc email để tìm quản trị viên.</p>`;
    search.oninput = () => {
        clearTimeout(managerSearchTimer);
        const query = search.value.trim();
        const requestId = ++managerSearchRequest;
        if (query.length < 2) {
            availableList.innerHTML = `<p class="field-hint">Nhập ít nhất 2 ký tự để tìm.</p>`;
            return;
        }
        availableList.innerHTML = `<p class="field-hint">Đang tìm...</p>`;
        managerSearchTimer = setTimeout(async () => {
            try {
                const candidates = await requestJson(
                    `/admin/judge/academic-classes/${classId}/available-managers?search=${encodeURIComponent(query)}`,
                );
                if (requestId !== managerSearchRequest) {
                    return;
                }
                availableList.innerHTML = candidates.map((candidate) => `
                    <div class="entity-row">
                        <span><strong>${escapeHtml(candidate.account_name)}</strong><small>${escapeHtml(candidate.account_email)}</small></span>
                        <button type="button" data-action="add-manager" data-account-id="${candidate.account_id}">Mời</button>
                    </div>
                `).join("") || `<p class="field-hint">Không tìm thấy quản trị viên phù hợp.</p>`;
            } catch (error) {
                if (requestId !== managerSearchRequest) {
                    return;
                }
                console.error(error);
                availableList.innerHTML = `<p class="field-hint error">${escapeHtml(error.message || "Không thể tìm quản trị viên.")}</p>`;
            }
        }, 250);
    };
}

async function loadAvailableMembers(classId, canManagePermissions) {
    const search = document.getElementById("member-search");
    const availableList = document.getElementById("available-member-list");
    const memberMessage = document.getElementById("member-message");
    if (!search || !availableList || !memberMessage) {
        return;
    }
    if (!canManagePermissions) {
        availableList.replaceChildren();
        memberMessage.textContent = "";
        search.oninput = null;
        return;
    }

    availableList.innerHTML = `<p class="field-hint">Nhập tên hoặc email để tìm thành viên.</p>`;
    search.oninput = () => {
        clearTimeout(memberSearchTimer);
        const query = search.value.trim();
        const requestId = ++memberSearchRequest;
        if (query.length < 2) {
            availableList.innerHTML = `<p class="field-hint">Nhập ít nhất 2 ký tự để tìm.</p>`;
            return;
        }
        availableList.innerHTML = `<p class="field-hint">Đang tìm...</p>`;
        memberSearchTimer = setTimeout(async () => {
            try {
                const candidates = await requestJson(
                    `/admin/judge/academic-classes/${classId}/available-students?search=${encodeURIComponent(query)}`,
                );
                if (requestId !== memberSearchRequest || classId !== activeClassId) {
                    return;
                }
                availableList.innerHTML = candidates.map((candidate) => `
                    <div class="entity-row">
                        <span><strong>${escapeHtml(candidate.account_name)}</strong><small>${escapeHtml(candidate.account_email)} · ${candidate.role === "ADMIN" ? "Quản trị viên" : candidate.role === "SUPER_ADMIN" ? "Quản trị cấp cao" : "Học viên"}</small></span>
                        <button type="button" data-action="add-member" data-account-id="${candidate.account_id}">Mời</button>
                    </div>
                `).join("") || `<p class="field-hint">Không tìm thấy thành viên phù hợp.</p>`;
            } catch (error) {
                if (requestId !== memberSearchRequest || classId !== activeClassId) {
                    return;
                }
                console.error(error);
                availableList.innerHTML = `<p class="field-hint error">${escapeHtml(error.message || "Không thể tìm thành viên.")}</p>`;
            }
        }, 250);
    };
}

async function loadSubmissionHistory(classId, { append = false } = {}) {
    const offset = append ? submissionHistoryOffset : 0;
    const history = await requestJson(
        `/student/classes/${classId}/submissions?limit=${submissionHistoryPageSize}&offset=${offset}`,
    );
    const list = document.getElementById("submission-history-list");
    renderSubmissionHistory(list, history, {
        append,
        emptyText: "Chưa có bài nộp nào trong lớp này.",
    });
    submissionHistoryOffset = offset + history.length;
    document.getElementById("class-submission-history-more").hidden =
        history.length < submissionHistoryPageSize;
}

async function initialize() {
    const params = new URLSearchParams(window.location.search);
    const classId = params.get("class_id");
    if (!classId || !/^\d+$/.test(classId)) {
        window.location.replace("classroom_list.html");
        return;
    }

    try {
        currentAccount = await requireAccountAccess({
            allowedRoles: ["USER", "ADMIN", "SUPER_ADMIN"],
            redirectTo: "login.html",
        });
        if (!currentAccount) {
            return;
        }
        const requestedListView = params.get("from");
        classListView = currentAccount.role !== "USER" && requestedListView !== "my-classes"
            ? "managed-classes"
            : "my-classes";
        const isStudent = currentAccount.role === "USER";
        const openAsStudent = isStudent || classListView === "my-classes";
        if (openAsStudent) {
            await openClass(classId, true);
        } else {
            document.getElementById("advanced-admin-link").hidden = false;
            await openClass(classId, false);
        }
    } catch (error) {
        console.error(error);
        setMessage(pageMessage, error.message || "Không thể tải lớp học.", true);
    }
}

if (showCreateClassButton) {
    showCreateClassButton.addEventListener("click", () => {
        if (!createClassForm) return;
        createClassForm.hidden = false;
        const newClassNameInput = document.getElementById("new-class-name");
        if (newClassNameInput) {
            newClassNameInput.focus();
        }
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
            setMessage(document.getElementById("create-class-message"), "Vui lòng nhập tên lớp học.", true);
            return;
        }

        button.disabled = true;
        try {
            const response = await apiFetch("/admin/judge/academic-classes", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ class_name: className }),
            });

            if (response.ok) {
                form.reset();
                form.hidden = true;
                setMessage(document.getElementById("create-class-message"), "Đã tạo lớp mới. Bạn đang là chủ lớp.", false);
                await loadAdminClasses();
            } else {
                let errorMsg = `Yêu cầu thất bại (${response.status})`;
                try {
                    const errorData = await readResponse(response);
                    if (errorData?.detail) errorMsg = errorData.detail;
                } catch {
                    const fallbackText = await response.text();
                    if (fallbackText) errorMsg = fallbackText;
                }
                setMessage(document.getElementById("create-class-message"), errorMsg, true);
            }
        } catch (networkError) {
            console.error("Lỗi tạo lớp:", networkError);
            setMessage(document.getElementById("create-class-message"), "Lỗi kết nối máy chủ.", true);
        } finally {
            button.disabled = false;
        }
    });
}

document.getElementById("contest-problem-picker").addEventListener("change", (event) => {
    const selectedCount = selectedContestProblemIds.length;
    document.getElementById("add-contest-problem").disabled =
        !event.currentTarget.value || selectedCount >= maxContestProblems;
});

document.getElementById("add-contest-problem").addEventListener("click", () => {
    const picker = document.getElementById("contest-problem-picker");
    const problemId = Number(picker.value);
    if (!problemId || selectedContestProblemIds.includes(problemId) ||
        selectedContestProblemIds.length >= maxContestProblems) {
        return;
    }
    selectedContestProblemIds.push(problemId);
    renderContestProblemSelection();
    setMessage(document.getElementById("create-contest-message"), "");
});

document.getElementById("contest-selected-problems").addEventListener("click", (event) => {
    const button = event.target.closest("[data-contest-action]");
    if (!button) {
        return;
    }

    const index = Number(button.dataset.index);
    if (!Number.isInteger(index) || index < 0 || index >= selectedContestProblemIds.length) {
        return;
    }
    if (button.dataset.contestAction === "remove") {
        selectedContestProblemIds.splice(index, 1);
    } else {
        const offset = button.dataset.contestAction === "up" ? -1 : 1;
        const nextIndex = index + offset;
        if (nextIndex < 0 || nextIndex >= selectedContestProblemIds.length) {
            return;
        }
        [selectedContestProblemIds[index], selectedContestProblemIds[nextIndex]] =
            [selectedContestProblemIds[nextIndex], selectedContestProblemIds[index]];
    }
    renderContestProblemSelection();
    setMessage(document.getElementById("create-contest-message"), "");
});

document.body.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    button.disabled = true;
    try {
        const classId = button.dataset.classId || activeClassId;
        switch (button.dataset.action) {
            case "open-admin-class":
                await openClass(classId, false);
                break;
            case "claim-class":
                await requestJson(`/admin/judge/academic-classes/${classId}/claim`, { method: "POST" });
                setMessage(pageMessage, "Bạn đã trở thành chủ lớp.");
                await loadAdminClasses();
                break;
            case "open-student-class":
                await openClass(classId, true);
                break;
            case "switch-to-student":
                await openClass(activeClassId, true);
                break;
            case "switch-to-management":
                await openClass(activeClassId, false);
                break;
            case "edit-contest": {
                const contest = (await requestJson(
                    `/admin/judge/academic-classes/${activeClassId}`,
                )).contests.find(
                    (item) => item.contest_id === Number(button.dataset.contestId),
                );
                if (!contest) {
                    throw new Error("Không tìm thấy cuộc thi cần sửa.");
                }
                editContest(contest);
                break;
            }
            case "request-join":
                await requestJson(`/student/classes/${classId}/join-requests`, { method: "POST" });
                setMessage(pageMessage, "Đã gửi yêu cầu. Bạn có thể vào lớp sau khi chủ lớp duyệt.");
                await loadStudentClasses();
                break;
            case "decide-request":
                await requestJson(
                    `/admin/judge/academic-classes/${activeClassId}/join-requests/${button.dataset.requestId}`,
                    {
                        method: "PATCH",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ status: button.dataset.status }),
                    },
                );
                await openClass(activeClassId, false);
                break;
            case "remove-member":
                await requestJson(
                    `/admin/judge/academic-classes/${activeClassId}/students/${button.dataset.accountId}`,
                    { method: "DELETE" },
                );
                await openClass(activeClassId, false);
                break;
            case "remove-manager":
                await requestJson(
                    `/admin/judge/academic-classes/${activeClassId}/managers/${button.dataset.accountId}`,
                    { method: "DELETE" },
                );
                await loadManagers(activeClassId, true);
                break;
            case "add-manager":
                await requestJson(`/admin/judge/academic-classes/${activeClassId}/managers`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ account_id: Number(button.dataset.accountId) }),
                });
                setMessage(document.getElementById("manager-message"), "Đã mời quản trị viên vào lớp.");
                await loadManagers(activeClassId, true);
                document.getElementById("manager-search").dispatchEvent(new Event("input"));
                break;
            case "add-member":
                await requestJson(`/admin/judge/academic-classes/${activeClassId}/students`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ account_id: Number(button.dataset.accountId) }),
                });
                setMessage(document.getElementById("member-message"), "Đã mời thành viên vào lớp.");
                await openClass(activeClassId, false);
                break;
        }
    } catch (error) {
        setMessage(pageMessage, error.message || "Thao tác không thành công.", true);
    } finally {
        button.disabled = false;
    }
});

document.getElementById("back-to-classes").addEventListener("click", () => {
    window.location.href = `classroom_list.html?view=${classListView}`;
});

document.getElementById("class-submission-history-more").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
        await loadSubmissionHistory(activeClassId, { append: true });
    } catch (error) {
        console.error(error);
        setMessage(pageMessage, error.message || "Không thể tải thêm lịch sử nộp bài.", true);
    } finally {
        button.disabled = false;
    }
});

document.getElementById("add-problem-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = document.getElementById("add-problem-button");
    const problemId = Number(new FormData(form).get("problem_id"));
    if (!problemId) {
        setMessage(document.getElementById("add-problem-message"), "Hãy chọn một bài tập.", true);
        return;
    }
    button.disabled = true;
    try {
        await requestJson(`/admin/judge/academic-classes/${activeClassId}/problems`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ problem_id: problemId, is_required: false }),
        });
        await openClass(activeClassId, false);
        setMessage(document.getElementById("add-problem-message"), "Đã thêm bài tập vào lớp.");
    } catch (error) {
        setMessage(
            document.getElementById("add-problem-message"),
            error.message || "Không thể thêm bài tập vào lớp.",
            true,
        );
        button.disabled = false;
    }
});

document.getElementById("create-contest-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    const values = new FormData(form);
    try {
        if (!selectedContestProblemIds.length) {
            throw new Error("Hãy thêm ít nhất một bài tập vào danh sách contest.");
        }
        if (selectedContestProblemIds.length > maxContestProblems) {
            throw new Error(`Một contest chỉ được có tối đa ${maxContestProblems} bài.`);
        }
        const problemCount = selectedContestProblemIds.length;
        const start = new Date(values.get("start_time"));
        const end = new Date(values.get("end_time"));
        if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) {
            throw new Error("Thời điểm kết thúc phải sau thời điểm bắt đầu.");
        }
        const contestId = form.dataset.contestId;
        const contest = await requestJson(
            contestId
                ? `/admin/judge/academic-classes/${activeClassId}/contests/${contestId}`
                : `/admin/judge/academic-classes/${activeClassId}/contests/create`,
            {
                method: contestId ? "PATCH" : "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    contest_name: values.get("contest_name"),
                    description: values.get("description"),
                    start_time: start.toISOString(),
                    end_time: end.toISOString(),
                    problem_ids: [...selectedContestProblemIds],
                }),
            },
        );
        resetContestForm();
        await openClass(activeClassId, false);
        setMessage(
            document.getElementById("create-contest-message"),
            contestId
                ? `Đã cập nhật cuộc thi "${contest.contest_name}" với ${problemCount} bài.`
                : `Đã tạo cuộc thi "${contest.contest_name}" với ${problemCount} bài.`,
        );
    } catch (error) {
        setMessage(document.getElementById("create-contest-message"), error.message, true);
    } finally {
        renderContestProblemSelection();
    }
});

document.getElementById("cancel-contest-edit").addEventListener("click", resetContestForm);

const logoutButton = document.getElementById("logout-button");
if (logoutButton) {
    logoutButton.addEventListener("click", async () => {
        try {
            const response = await apiFetch("/auth/logout", { method: "POST" });
            if (response.ok) {
                window.location.replace("login.html");
                return;
            }
            setMessage(pageMessage, `Không thể đăng xuất (${response.status}).`, true);
        } catch (error) {
            setMessage(pageMessage, error.message || "Không thể kết nối đến server.", true);
        }
    });
}

initialize();
