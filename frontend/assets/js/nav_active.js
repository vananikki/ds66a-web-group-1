(function () {
  function initHeaderAndNav() {
    const header = document.querySelector(".site-header, header");
    const nav = header ? header.querySelector("nav, .nav-links") : null;

    // 1. Tự động thêm nút Hamburger nếu đang ở màn hình có Header
    if (header && nav && !document.getElementById("mobile-menu-btn")) {
      const toggleBtn = document.createElement("button");
      toggleBtn.id = "mobile-menu-btn";
      toggleBtn.className = "mobile-toggle-btn";
      toggleBtn.setAttribute("type", "button");
      toggleBtn.setAttribute("aria-label", "Mở menu");
      toggleBtn.innerHTML = `
        <span></span>
        <span></span>
        <span></span>
      `;

      // Bấm nút để đóng/mở menu
      toggleBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleBtn.classList.toggle("open");
        nav.classList.toggle("show");
      });

      // Đóng menu khi bấm ra ngoài
      document.addEventListener("click", (e) => {
        if (!header.contains(e.target)) {
          toggleBtn.classList.remove("open");
          nav.classList.remove("show");
        }
      });

      // Chèn nút hamburger vào trước nav
      header.insertBefore(toggleBtn, nav);
    }

    // 2. Tự động highlight tab active
    const currentPath = window.location.pathname.split("/").filter(Boolean).pop() || "index.html";
    const pageTitle = (document.querySelector("h1")?.textContent || "").trim().toLowerCase();
    const navLinks = document.querySelectorAll(".site-header a, header nav a, .topbar a");

    navLinks.forEach((link) => {
      if (link.id === "logout-button" || link.classList.contains("brand")) return;

      const href = link.getAttribute("href") || "";
      const cleanHref = href.split("?")[0].split("#")[0].split("/").filter(Boolean).pop();
      const linkText = link.textContent.trim().toLowerCase();

      const isMatch = (cleanHref && currentPath === cleanHref) ||
                      (pageTitle && linkText && pageTitle.includes(linkText));

      if (isMatch) {
        link.classList.add("active");
        link.setAttribute("aria-current", "page");
      } else {
        link.classList.remove("active");
        link.removeAttribute("aria-current");
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initHeaderAndNav);
  } else {
    initHeaderAndNav();
  }
})();