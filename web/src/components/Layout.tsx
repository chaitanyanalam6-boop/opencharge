import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

import { Wordmark } from "./Icons";
import styles from "./Layout.module.css";

export const NOTICE = "Built from public CMS price transparency files; not medical or billing advice.";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className={styles.shell}>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>
      <header className={styles.header}>
        <div className={`container ${styles.headerInner}`}>
          <Link to="/" className={styles.brand}>
            <Wordmark />
            <span>OpenCharge</span>
          </Link>
          <nav aria-label="Main" className={styles.nav}>
            <NavLink to="/" end className={({ isActive }) => (isActive ? styles.active : undefined)}>
              Search
            </NavLink>
            <NavLink to="/status" className={({ isActive }) => (isActive ? styles.active : undefined)}>
              Data status
            </NavLink>
          </nav>
        </div>
      </header>
      <main id="main" className={styles.main}>
        {children}
      </main>
      <footer className={styles.footer}>
        <div className="container">
          <p>{NOTICE}</p>
          <p className={styles.footerSmall}>
            Every price links to the row it came from in the hospital's own file.{" "}
            <Link to="/status">How the data is collected</Link>
          </p>
        </div>
      </footer>
    </div>
  );
}
