const sections = [
  "Draft-first capture",
  "Strict confirmed ledger",
  "CLI and Agent command surface",
  "Audit, rollback, and security gates"
];

export default function Page() {
  return (
    <main className="shell">
      <section className="placeholder" aria-label="Track Anywhere placeholder">
        <p className="eyebrow">Track Anywhere</p>
        <h1>Personal Accounting Workspace</h1>
        <p className="summary">
          Frontend development is paused. This placeholder keeps the Next.js app in the repository while the backend,
          CLI, and data model continue to evolve first.
        </p>
        <ul className="scope-list">
          {sections.map((section) => (
            <li key={section}>{section}</li>
          ))}
        </ul>
      </section>
    </main>
  );
}
