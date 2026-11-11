import { apiFetch, readResponse, requireAccountAccess } from "./api.js?v=20261029a";
import { typesetMath } from "./latex.js?v=20261101b";
import { renderSubmissionHistory } from "./submission_history.js?v=20261102b";
import { initCodeEditor } from "./code_editor.js?v=20261111a";
import { attachCodeRunner } from "./code_runner.js?v=20261111b";

const params = new URLSearchParams(window.location.search);
const classId = params.get("class_id");
const contestId = params.get("contest_id");
const pageMessage = document.getElementById("contest-message");
const countdownElement = document.getElementById("contest-countdown");
const submitButton = document.getElementById("submit-solution");
const runButton = document.getElementById("run-contest-code");
const historyPageSize = 50;
let contestData;
let selectedProblem;
let serverClockOffset = 0;
let historyOffset = 0;
let polling = false;
let contestEnded = false;
let pollTimer;
let countdownTimer;
let editor;
const submissionById = new Map();

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
        const error = new Error(data.detail || `Yêu cầu thất bại (${response.status})`);
        error.status = response.status;
        throw error;
    }
    return data;
}

function updateServerClock(serverTime) {
    serverClockOffset = Date.parse(serverTime) - Date.now();
}

function finishContest(message) {
    if (contestEnded) return;
    contestEnded = true;
    countdownElement.textContent = "00:00:00";
    document.getElementById("contest-status").textContent = "ĐÃ KẾT THÚC";
    submitButton.disabled = true;
    runButton.disabled = true;
    if (message) setMessage(pageMessage, message, true);
    clearInterval(pollTimer);
    clearInterval(countdownTimer);
}

function renderCountdown() {
    if (!contestData || contestEnded) {
        return;
    }
    const now = Date.now() + serverClockOffset;
    const startTime = Date.parse(contestData.start_time);
    const endTime = Date.parse(contestData.end_time);
    const status = now < startTime ? "upcoming" : now <= endTime ? "active" : "finished";
    document.getElementById("contest-status").textContent =
        status === "upcoming" ? "SẮP DIỄN RA" : status === "active" ? "ĐANG DIỄN RA" : "ĐÃ KẾT THÚC";
    document.getElementById("contest-countdown-label").textContent =
        status === "upcoming" ? "Bắt đầu sau" : status === "active" ? "Còn lại" : "Đã kết thúc";
    const remaining = (status === "upcoming" ? startTime : endTime) - now;
    if (remaining <= 0) {
        if (status === "finished") {
            finishContest(
                contestData.can_submit
                    ? "Cuộc thi đã kết thúc. Không thể tiếp tục nộp bài."
                    : "",
            );
        }
        return;
    }
    const seconds = Math.floor(remaining / 1000);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    countdownElement.textContent =
        `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function renderProblem(problem) {
    selectedProblem = problem;
    document.querySelectorAll("[data-problem-id]").forEach((button) => {
        button.classList.toggle("active", Number(button.dataset.problemId) === problem.problem_id);
        button.setAttribute(
            "aria-current",
            Number(button.dataset.problemId) === problem.problem_id ? "true" : "false",
        );
    });
    document.getElementById("statement-problem-order").textContent =
        `BÀI ${problem.problem_order} · #${problem.problem_id}`;
    document.getElementById("statement-title").textContent = problem.problem_name;
    for (const [field, sectionId, contentId] of [
        ["description", "statement-description-section", "statement-description"],
        ["input_description", "statement-input-section", "statement-input"],
        ["output_description", "statement-output-section", "statement-output"],
        ["data_constraints", "statement-constraints-section", "statement-constraints"],
    ]) {
        const content = String(problem[field] ?? "");
        document.getElementById(contentId).textContent = content;
        document.getElementById(sectionId).hidden = !content.trim();
    }
    const limits = [];
    if (problem.time_limit) limits.push(`Thời gian: ${problem.time_limit} giây`);
    if (problem.memory_limit) limits.push(`Bộ nhớ: ${problem.memory_limit} MB`);
    document.getElementById("statement-limits").textContent = limits.join(" · ");
    document.getElementById("contest-problem-statement").hidden = false;
    typesetMath(document.getElementById("contest-problem-statement")).catch((error) => {
        console.error(error);
        setMessage(pageMessage, error.message || "Không thể hiển thị công thức LaTeX.", true);
    });
}

function renderProblemPicker(problems) {
    const picker = document.getElementById("contest-problem-picker");
    picker.innerHTML = (Array.isArray(problems) ? problems : []).map((problem) => `
        <button class="contest-problem-choice${selectedProblem?.problem_id === problem.problem_id ? " active" : ""}" type="button" data-problem-id="${problem.problem_id}" aria-current="${selectedProblem?.problem_id === problem.problem_id}">
            <span>Bài ${problem.problem_order}</span>
            <strong>${escapeHtml(problem.problem_name)}</strong>
            ${problem.is_solved ? `<span class="status-pill">Đã giải</span>` : ""}
        </button>
    `).join("");
}

function activateContestTab(tab, { focus = false } = {}) {
    const tabs = [...document.querySelectorAll("[data-contest-tab]")];
    tabs.forEach((item) => {
        const selected = item === tab;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-selected", String(selected));
        item.tabIndex = selected ? 0 : -1;
        document.getElementById(item.dataset.contestTab).hidden = !selected;
    });
    if (focus) tab.focus();
}

function renderStandings(standings, problems) {
    const container = document.getElementById("contest-standings");
    const participants = Array.isArray(standings) ? standings : [];
    const contestProblems = Array.isArray(problems) ? problems : [];
    document.getElementById("contest-participant-count").textContent =
        `${participants.length} thí sinh`;
    if (!participants.length) {
        container.innerHTML = `<p class="field-hint">Chưa có thí sinh nộp bài.</p>`;
        return;
    }
    const gridStyle = `--contest-problem-count:${contestProblems.length}`;
    const problemHeaders = contestProblems.map((problem) => `
        <span title="${escapeHtml(problem.problem_name)}">Bài ${problem.problem_order}</span>
    `).join("");
    container.innerHTML = `
        <div class="contest-standing-row contest-standing-header" style="${gridStyle}" aria-hidden="true">
            <span>Hạng</span>
            <span>Thí sinh</span>
            ${problemHeaders}
            <span>Đúng</span>
            <span>Phạt</span>
            <span>Tổng điểm</span>
        </div>
        ${participants.map((item) => {
            const problemScores = new Map(
                (Array.isArray(item.problems) ? item.problems : [])
                    .map((problem) => [problem.problem_id, problem]),
            );
            const scoreCells = contestProblems.map((problem) => {
                const score = problemScores.get(problem.problem_id);
                return `<span class="contest-component-score${score?.solved ? " accepted" : ""}" title="${escapeHtml(problem.problem_name)}">${score?.score ?? 0} / ${score?.max_score ?? problem.max_score}</span>`;
            }).join("");
            return `
            <div class="contest-standing-row ${item.is_current_user ? "current-participant" : ""}" style="${gridStyle}">
                <strong>#${item.rank}</strong>
                <strong>${escapeHtml(item.account_name)}${item.is_current_user ? " (Bạn)" : ""}</strong>
                ${scoreCells}
                <span>${item.solved}</span>
                <span>${item.penalty}</span>
                <strong>${item.score}</strong>
            </div>
        `;
        }).join("")}`;
}

function renderMergedHistory() {
    const history = [...submissionById.values()].sort((first, second) =>
        Date.parse(second.submitted_at) - Date.parse(first.submitted_at),
    );
    renderSubmissionHistory(
        document.getElementById("contest-submission-history"),
        history,
        {
            emptyText: "Cuộc thi chưa có bài nộp nào.",
            sourceUrlForSubmission: contestData?.can_view_all_submissions
                ? (submission) =>
                    `/student/classes/${classId}/contests/${contestId}/submissions/${submission.submission_id}/source`
                : null,
        },
    );
}

async function fetchLatestHistory() {
    const history = await requestJson(
        `/student/classes/${classId}/submissions?contest_id=${contestId}&limit=${historyPageSize}&offset=0`,
    );
    history.forEach((submission) => submissionById.set(submission.submission_id, submission));
    historyOffset = Math.max(historyOffset, history.length);
    document.getElementById("load-older-submissions").hidden =
        history.length < historyPageSize;
    renderMergedHistory();
}

async function refreshLiveData() {
    if (polling || document.visibilityState !== "visible") return;
    polling = true;
    try {
        const [latestContest, standings] = await Promise.all([
            requestJson(`/student/classes/${classId}/contests/${contestId}`),
            requestJson(`/student/classes/${classId}/contests/${contestId}/standings`),
        ]);
        contestData = latestContest;
        updateServerClock(latestContest.server_time);
        const latestProblems = Array.isArray(latestContest.problems)
            ? latestContest.problems
            : [];
        renderProblemPicker(latestProblems);
        selectedProblem = latestProblems.find(
            (problem) => problem.problem_id === selectedProblem?.problem_id,
        ) || selectedProblem;
        renderStandings(
            standings.standings,
            standings.problems || contestData.problems,
        );
        await fetchLatestHistory();
        submitButton.disabled = !contestData.can_submit;
        runButton.disabled = contestEnded || !contestData.can_submit || !selectedProblem;
        renderCountdown();
    } catch (error) {
        if (error.status === 409) {
            finishContest(error.message);
        } else {
            console.error(error);
            setMessage(pageMessage, error.message || "Không thể cập nhật dữ liệu cuộc thi.", true);
        }
    } finally {
        polling = false;
    }
}

document.getElementById("contest-problem-picker").addEventListener("click", (event) => {
    const button = event.target.closest("[data-problem-id]");
    if (!button || !contestData) return;
    const problem = contestData.problems.find(
        (item) => item.problem_id === Number(button.dataset.problemId),
    );
    if (problem) renderProblem(problem);
});

async function loadOlderHistory() {
    const history = await requestJson(
        `/student/classes/${classId}/submissions?contest_id=${contestId}&limit=${historyPageSize}&offset=${historyOffset}`,
    );
    history.forEach((submission) => submissionById.set(submission.submission_id, submission));
    historyOffset += history.length;
    document.getElementById("load-older-submissions").hidden =
        history.length < historyPageSize;
    renderMergedHistory();
}

async function initialize() {
    if (!/^\d+$/.test(classId || "") || !/^\d+$/.test(contestId || "")) {
        setMessage(pageMessage, "Đường dẫn cuộc thi không hợp lệ.", true);
        return;
    }
    const from = params.get("from") === "managed-classes" ? "managed-classes" : "my-classes";
    document.getElementById("back-to-class").href =
        `classroom.html?class_id=${encodeURIComponent(classId)}&from=${from}#class-contest-list`;

    try {
        const account = await requireAccountAccess({
            allowedRoles: ["USER", "ADMIN", "SUPER_ADMIN"],
            redirectTo: "login.html",
        });
        if (!account) return;

        contestData = await requestJson(
            `/student/classes/${classId}/contests/${contestId}`,
        );
        if (contestData.status === "upcoming" && !contestData.can_manage) {
            throw new Error("Bạn chỉ có thể xem cuộc thi sau khi cuộc thi bắt đầu.");
        }
        document.querySelector(".contest-submit-panel").hidden = !contestData.can_submit;
        document.getElementById("contest-view-only-note").hidden = contestData.can_submit;
        document.getElementById("contest-main-panel").classList.toggle(
            "contest-view-only",
            !contestData.can_submit,
        );
        updateServerClock(contestData.server_time);
        document.title = `${contestData.contest_name} | Online Judge`;
        document.getElementById("contest-title").textContent = contestData.contest_name;
        document.getElementById("contest-description").textContent = contestData.description || "";
        document.getElementById("contest-schedule").textContent =
            `${new Date(contestData.start_time).toLocaleString()} – ${new Date(contestData.end_time).toLocaleString()}`;
        const contestProblems = Array.isArray(contestData.problems)
            ? contestData.problems
            : [];
        renderProblemPicker(contestProblems);
        if (contestProblems.length) {
            const requestedProblem = Number(params.get("problem_id"));
            const selected = contestProblems.find((item) => item.problem_id === requestedProblem)
                || contestProblems[0];
            renderProblem(selected);
        }
        if (contestData.can_submit) {
            const languages = await requestJson("/student/languages");
            document.getElementById("solution-language").innerHTML = languages.map((language) =>
                `<option value="${language.language_id}">${escapeHtml(language.name)}</option>`
            ).join("");
            if (!languages.length) {
                throw new Error("Chưa có ngôn ngữ chấm bài. Hãy đồng bộ danh sách Judge0 languages trước.");
            }
            const editorInstance = initCodeEditor(
                document.getElementById("contest-source-code"),
                document.getElementById("solution-language"),
            );
            editor = editorInstance?.editor;
            attachCodeRunner({
                button: runButton,
                input: document.getElementById("contest-run-input"),
                status: document.getElementById("contest-run-status"),
                stdout: document.getElementById("contest-run-output"),
                errors: document.getElementById("contest-run-errors"),
                languageSelect: document.getElementById("solution-language"),
                getSourceCode: () => editor
                    ? editor.getValue()
                    : document.getElementById("contest-source-code").value,
                getRequest: () => ({
                    path: `/student/classes/${encodeURIComponent(classId)}/problems/${selectedProblem.problem_id}/run`,
                    extraPayload: { contest_id: Number(contestId) },
                }),
                isAllowed: () => !contestEnded && Boolean(contestData?.can_submit && selectedProblem),
            });
            runButton.disabled = !contestData.can_submit || !selectedProblem;
        }
        const standings = await requestJson(
            `/student/classes/${classId}/contests/${contestId}/standings`,
        );
        renderStandings(
            standings.standings,
            standings.problems || contestData.problems,
        );
        await fetchLatestHistory();
        renderCountdown();
        if (contestData.status !== "finished") {
            countdownTimer = setInterval(renderCountdown, 1000);
            pollTimer = setInterval(refreshLiveData, 5000);
        }
    } catch (error) {
        console.error(error);
        setMessage(pageMessage, error.message || "Không thể tải cuộc thi.", true);
        submitButton.disabled = true;
    }
}

document.getElementById("contest-submit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedProblem || !contestData?.can_submit) {
        setMessage(pageMessage, "Bạn cần là thành viên lớp để nộp bài trong cuộc thi.", true);
        return;
    }
    const form = event.currentTarget;
    submitButton.disabled = true;
    setMessage(document.getElementById("submission-result"), "Đang gửi lời giải tới hệ thống chấm bài...");
    const values = new FormData(form);
    const sourceCode = editor ? editor.getValue() : values.get("source_code");
    if (!String(sourceCode || "").trim()) {
        setMessage(document.getElementById("submission-result"), "Vui lòng nhập mã nguồn trước khi nộp.", true);
        editor?.focus();
        submitButton.disabled = contestEnded || !contestData?.can_submit;
        runButton.disabled = contestEnded || !contestData?.can_submit || !selectedProblem;
        return;
    }
    try {
        const result = await requestJson(
            `/student/classes/${classId}/problems/${selectedProblem.problem_id}/submissions`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    language_id: Number(values.get("language_id")),
                    source_code: sourceCode,
                    contest_id: Number(contestId),
                }),
            },
        );
        setMessage(
            document.getElementById("submission-result"),
            `${result.status} · ${result.score} điểm · ${result.passed_testcases}/${result.total_testcases} test case · tổng ${result.runtime_ms == null ? "—" : `${result.runtime_ms} ms`} · bộ nhớ đỉnh ${result.memory_kb == null ? "—" : `${result.memory_kb} KB`}`,
            result.status !== "Accepted",
        );
        await refreshLiveData();
    } catch (error) {
        setMessage(
            document.getElementById("submission-result"),
            error.message || "Không thể chấm lời giải.",
            true,
        );
        if (error.status === 409) await refreshLiveData();
    } finally {
        submitButton.disabled = contestEnded || !contestData?.can_submit;
        renderCountdown();
    }
});

document.getElementById("load-older-submissions").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
        await loadOlderHistory();
    } catch (error) {
        console.error(error);
        setMessage(pageMessage, error.message || "Không thể tải thêm lịch sử.", true);
    } finally {
        button.disabled = false;
    }
});

const contestTabs = [...document.querySelectorAll("[data-contest-tab]")];
contestTabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activateContestTab(tab));
    tab.addEventListener("keydown", (event) => {
        const nextIndex = event.key === "ArrowRight"
            ? (index + 1) % contestTabs.length
            : event.key === "ArrowLeft"
                ? (index - 1 + contestTabs.length) % contestTabs.length
                : -1;
        if (nextIndex < 0) return;
        event.preventDefault();
        activateContestTab(contestTabs[nextIndex], { focus: true });
    });
});

document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
        refreshLiveData();
    }
});

window.addEventListener("pagehide", () => {
    clearInterval(pollTimer);
    clearInterval(countdownTimer);
});

const logoutButton = document.getElementById("logout-button");
logoutButton.addEventListener("click", async () => {
    try {
        const response = await apiFetch("/auth/logout", { method: "POST" });
        if (!response.ok) throw new Error(`Không thể đăng xuất (${response.status}).`);
        window.location.replace("login.html");
    } catch (error) {
        setMessage(pageMessage, error.message || "Không thể kết nối đến server.", true);
    }
});

initialize();
