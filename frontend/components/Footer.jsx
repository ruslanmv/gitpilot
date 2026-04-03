import React from "react";

export default function Footer() {
  return (
    <footer className="gp-footer">
      <div className="gp-footer-left">
        <a
          href="https://github.com/ruslanmv/gitpilot"
          target="_blank"
          rel="noopener noreferrer"
          style={{
            color: "inherit",
            textDecoration: "none",
            display: "flex",
            alignItems: "center",
            gap: "6px",
            transition: "color 0.2s ease",
          }}
          onMouseOver={(e) => {
            e.currentTarget.style.color = "#ff7a3c";
          }}
          onMouseOut={(e) => {
            e.currentTarget.style.color = "#c3c5dd";
          }}
        >
          ⭐ Star our GitHub project
        </a>
      </div>
      <div className="gp-footer-right">
        <span>© 2025 GitPilot</span>
        <a
          href="https://github.com/ruslanmv/gitpilot"
          target="_blank"
          rel="noopener noreferrer"
        >
          Docs
        </a>
        <a
          href="https://github.com/ruslanmv/gitpilot"
          target="_blank"
          rel="noopener noreferrer"
        >
          GitHub
        </a>
      </div>
    </footer>
  );
}