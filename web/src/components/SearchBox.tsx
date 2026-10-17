import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { SearchIcon } from "./Icons";
import styles from "./SearchBox.module.css";
import { ui } from "./ui";

export function SearchBox({ initial = "", large = false }: { initial?: string; large?: boolean }) {
  const [query, setQuery] = useState(initial);
  const navigate = useNavigate();

  function submit(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (q) navigate(`/results?q=${encodeURIComponent(q)}`);
  }

  return (
    <form role="search" onSubmit={submit} className={`${styles.form} ${large ? styles.large : ""}`}>
      <label htmlFor={large ? "q-hero" : "q"} className={large ? styles.heroLabel : "visually-hidden"}>
        What do you need, and where?
      </label>
      <div className={styles.row}>
        <div className={styles.inputWrap}>
          <SearchIcon />
          <input
            id={large ? "q-hero" : "q"}
            name="q"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="knee MRI near Baltimore with Aetna"
            maxLength={300}
            autoComplete="off"
            className={styles.input}
          />
        </div>
        <button type="submit" className={ui.button}>
          Search prices
        </button>
      </div>
    </form>
  );
}
