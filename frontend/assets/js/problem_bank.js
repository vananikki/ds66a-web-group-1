import { apiFetch, readResponse } from "./api.js?v=20261029a";
import { typesetMath } from "./latex.js?v=20261101b";

const publicList = document.getElementById("public-problem-list");
const ownedList = document.getElementById("owned-problem-list");
const pageMessage = document.getElementById("page-message");
const publicListCount = document.getElementById("public-problem-list-count");
const ownedListCount = document.getElementById("owned-problem-list-count");
let account;
let searchTimer;
let searchRequestId = 0;

async function requestJson(path) {
    const response = await apiFetch(path);
    const data = await readResponse(response);
    if (!response.ok) {
        throw new Error(data.detail || `Yêu cầu thất bại (${response.status})`);
    }
    return data;
}

function renderProblemPicker(element, problems, { isPublic = false, query = "" } = {}) {
    element.replaceChildren();
    const count = problems.length;
    const listCount = isPublic ? publicListCount : ownedListCount;
    listCount.textContent = `${count} bài`;

    if (!problems.length) {
        const empty = document.createElement("p");
        empty.className = "field-hint";
        empty.textContent = query ? "Không tìm thấy bài phù hợp." : "Chưa có bài tập nào.";
        element.append(empty);
        return;
    }

    for (const problem of problems) {
        const row = document.createElement("div");
        row.className = "problem-picker-row";
        const open = document.createElement("a");
        const params = new URLSearchParams({
            problem_id: String(problem.problem_id),
            source: isPublic ? "public" : "owner",
        });
        open.className = "problem-picker-item";
        open.href = `problem_submit.html?${params}`;
        const number = document.createElement("span");
        number.className = "problem-picker-number";
        number.textContent = `#${problem.problem_id}`;
        const copy = document.createElement("span");
        copy.className = "problem-picker-copy";
        const title = document.createElement("strong");
        title.textContent = problem.problem_name;
        const description = document.createElement("small");
        const summary = (problem.description || "").replace(/\s+/g, " ").trim();
        description.textContent = summary
            ? `${summary.slice(0, 100)}${summary.length > 100 ? "…" : ""}`
            : "Mở đề bài và bắt đầu nộp lời giải.";
        const arrow = document.createElement("span");
        arrow.className = "problem-picker-arrow";
        arrow.setAttribute("aria-hidden", "true");
        arrow.textContent = "→";
        copy.append(title, description);
        open.append(number, copy, arrow);
        row.append(open);

        if (!isPublic && problem.created_by === account.account_id) {
            const edit = document.createElement("a");
            edit.className = "problem-picker-edit";
            edit.href = `create_problem.html?problem_id=${encodeURIComponent(problem.problem_id)}`;
            edit.textContent = "Sửa";
            edit.setAttribute("aria-label", `Sửa ${problem.problem_name}`);
            row.append(edit);
        }
        element.append(row);
    }
}

function setMessage(text) {
    pageMessage.textContent = text;
    pageMessage.classList.add("error");
}

async function initialize() {
    try {
        const accountResponse = await apiFetch("/auth/me");
        if (!accountResponse.ok) {
            window.location.replace("login.html");
            return;
        }
        account = await accountResponse.json();
        const problems = await requestJson("/student/problems");
        renderProblemPicker(publicList, problems, { isPublic: true });

        if (account.role === "ADMIN" || account.role === "SUPER_ADMIN") {
            document.getElementById("create-problem-main-link").hidden = false;
            document.getElementById("owned-problems-panel").hidden = false;
            document.getElementById("problem-bank-picker-layout").classList.add("has-owned-problems");
            const ownedProblems = await requestJson("/admin/judge/problems");
            renderProblemPicker(
                ownedList,
                ownedProblems.filter((problem) => problem.created_by === account.account_id),
            );
        }
        document.getElementById("problem-filter").disabled = false;

        // Biên dịch LaTeX cho toàn bộ danh sách bài tập vừa render
        await typesetMath(document.getElementById("problem-bank-picker-layout") || document.body);
    } catch (error) {
        console.error(error);
        setMessage(error instanceof Error ? error.message : "Không thể tải kho bài tập.");
    }
}

async function searchProblems(query) {
    const requestId = ++searchRequestId;
    const status = document.getElementById("problem-search-status");
    status.textContent = query ? "Đang tìm trong kho bài..." : "";
    pageMessage.textContent = "";
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    const suffix = params.size ? `?${params}` : "";
    try {
        const publicResults = requestJson(`/student/problems${suffix}`);
        const ownedResults = account.role === "ADMIN" || account.role === "SUPER_ADMIN"
            ? requestJson(`/admin/judge/problems${suffix}`)
            : Promise.resolve([]);
        const [problems, owned] = await Promise.all([publicResults, ownedResults]);
        if (requestId !== searchRequestId) return;
        renderProblemPicker(publicList, problems, { isPublic: true, query });
        if (!document.getElementById("owned-problems-panel").hidden) {
            renderProblemPicker(
                ownedList,
                owned.filter((problem) => problem.created_by === account.account_id),
                { query },
            );
        }
        const count = problems.length +
            owned.filter((problem) => problem.created_by === account.account_id).length;
        status.textContent = query ? `${count} bài phù hợp` : "";

        // Biên dịch lại LaTeX sau khi lọc/tìm kiếm bài tập
        await typesetMath(document.getElementById("problem-bank-picker-layout") || document.body);
    } catch (error) {
        if (requestId !== searchRequestId) return;
        console.error(error);
        status.textContent = "";
        setMessage(error instanceof Error ? error.message : "Không thể tìm bài tập.");
    }
}

document.getElementById("problem-filter").addEventListener("input", (event) => {
    const query = event.currentTarget.value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => searchProblems(query), 250);
});

const logoutButton = document.getElementById("logout-button");
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
            setMessage(error instanceof Error ? error.message : "Không thể đăng xuất.");
        }
    });
}

initialize();