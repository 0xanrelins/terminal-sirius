import { useEffect, useRef, useState } from "react";
import { useFeed } from "../context/FeedContext";
import styles from "./TopBar.module.css";

type Props = {
  dashboardNames: string[];
  active: string;
  onSwitch: (name: string) => void;
  onAdd: () => void;
  onRename: (oldName: string, newName: string) => void;
  onDelete: (name: string) => void;
};

export function TopBar({ dashboardNames, active, onSwitch, onAdd, onRename, onDelete }: Props) {
  const { status } = useFeed();
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  function startEdit(name: string) {
    setEditing(name);
    setEditValue(name);
  }

  function commitEdit() {
    const trimmed = editValue.trim();
    if (editing && trimmed && trimmed !== editing && !dashboardNames.includes(trimmed)) {
      onRename(editing, trimmed);
    }
    setEditing(null);
  }

  function onEditKey(e: React.KeyboardEvent) {
    if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
    if (e.key === "Escape") setEditing(null);
  }

  return (
    <header className={styles.topbar}>
      <span className={styles.logo}>SIRIUS</span>

      <nav className={styles.tabs}>
        {dashboardNames.map((name) => (
          <div
            key={name}
            className={`${styles.tab} ${active === name ? styles.activeTab : ""}`}
          >
            {editing === name ? (
              <input
                ref={inputRef}
                className={styles.tabInput}
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onBlur={commitEdit}
                onKeyDown={onEditKey}
              />
            ) : (
              <button
                className={styles.tabLabel}
                onClick={() => onSwitch(name)}
                onDoubleClick={() => startEdit(name)}
                title="Double-click to rename"
              >
                {name}
              </button>
            )}
            {dashboardNames.length > 1 && (
              <button
                className={styles.tabDelete}
                onClick={() => onDelete(name)}
                title="Delete dashboard"
              >
                ×
              </button>
            )}
          </div>
        ))}

        <button className={styles.addTab} onClick={onAdd} title="New dashboard">
          +
        </button>
      </nav>

      <div className={styles.right}>
        <span className={`${styles.statusDot} ${styles[status]}`} title={`WebSocket: ${status}`} />
      </div>
    </header>
  );
}
