import { useState, useEffect, useCallback, type FormEvent } from "react";
import {
  getAccounts,
  postAccount,
  patchAccount,
  deleteAccount,
  getAccountHealth,
  type Account,
  type AccountHealth,
} from "../api";
import { Toggle } from "../components/Toggle";
import { motion, AnimatePresence } from "framer-motion";
import {
  Trash2,
  Plus,
  Shield,
  KeyRound,
  AlertCircle,
  CheckCircle2,
  User,
} from "lucide-react";

// ─── Account row ──────────────────────────────────────────────────────────────

interface AccountRowProps {
  acc: Account;
  health?: { ok: boolean; error: string };
  onModeToggle: (acc: Account) => void;
  onEnabledToggle: (acc: Account) => void;
  onDelete: (acc: Account) => void;
}

function AccountRow({
  acc,
  health,
  onModeToggle,
  onEnabledToggle,
  onDelete,
}: AccountRowProps) {
  const isLive = acc.mode === "LIVE";

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-4 px-5 py-3.5">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-[#1A1F2E] flex items-center justify-center shrink-0">
            <User size={14} className="text-[#5A6478]" />
          </div>
          <div className="min-w-0">
            <p className="font-semibold text-gray-100 truncate text-sm">
              {acc.name}
            </p>
            <p className="text-xs text-[#5A6478] font-mono">{acc.client_id}</p>
          </div>
          <span
            className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
              acc.broker === "ZERODHA"
                ? "bg-purple-500/10 text-purple-400 border border-purple-500/30"
                : "bg-blue-500/10 text-blue-400 border border-blue-500/30"
            }`}
          >
            {acc.broker || "DHAN"}
          </span>
          {acc.broker === "ZERODHA" && (
            <a
              href={`/api/zerodha/login?client_id=${acc.client_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="px-3 py-1 rounded-lg text-[10px] font-bold border transition-all duration-150
                bg-purple-500/10 border-purple-500/30 text-purple-400 hover:bg-purple-500/20"
            >
              Login to Zerodha
            </a>
          )}
          {/* Token health indicator */}
          {health ? (
            health.ok ? (
              <span
                className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold
                bg-[#00E676]/10 text-[#00E676] border border-[#00E676]/25"
                title="Token is valid"
              >
                <CheckCircle2 size={10} /> Token OK
              </span>
            ) : (
              <span
                className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold
                bg-[#FF5252]/10 text-[#FF5252] border border-[#FF5252]/25"
                title={health.error}
              >
                <AlertCircle size={10} /> Token Expired
              </span>
            )
          ) : (
            <span
              className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold
              bg-[#1A1F2E] text-[#5A6478] border border-[#2A3045]"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-[#5A6478] animate-pulse" />{" "}
              Checking...
            </span>
          )}
        </div>

        {/* Mode pill */}
        <button
          type="button"
          onClick={() => onModeToggle(acc)}
          className={`px-3 py-1 rounded-lg text-xs font-bold border transition-all duration-150 ${
            isLive
              ? "bg-[#00E676]/10 border-[#00E676]/30 text-[#00E676] hover:bg-[#00E676]/20"
              : "bg-[#1A1F2E] border-[#2A3045] text-[#5A6478] hover:border-[#5A6478]"
          }`}
        >
          {isLive ? "● LIVE" : "○ PAPER"}
        </button>

        {/* Enabled toggle */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-[#5A6478]">Enabled</span>
          <Toggle
            checked={acc.enabled === 1}
            onChange={() => onEnabledToggle(acc)}
          />
        </div>

        {/* Delete */}
        <button
          type="button"
          onClick={() => onDelete(acc)}
          className="p-1.5 rounded-md text-[#3A4255] hover:text-[#FF5252] hover:bg-[#FF5252]/10 transition-all duration-150"
          aria-label="Delete account"
        >
          <Trash2 size={13} />
        </button>
      </div>

    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function Accounts() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Market data token
  const [mdTokenMasked, setMdTokenMasked] = useState("");
  const [mdClientId, setMdClientId] = useState("");
  const [mdNewToken, setMdNewToken] = useState("");
  const [mdNewClientId, setMdNewClientId] = useState("");
  const [mdSaveMsg, setMdSaveMsg] = useState("");

  // Add form
  const [name, setName] = useState("");
  const [clientId, setClientId] = useState("");
  const [accessToken, setToken] = useState("");
  const [broker, setBroker] = useState<"DHAN" | "ZERODHA">("DHAN");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [addError, setAddError] = useState("");
  const [adding, setAdding] = useState(false);
  const [addSuccess, setAddSuccess] = useState(false);

  // Health check state
  const [healthMap, setHealthMap] = useState<AccountHealth>({});

  const load = useCallback(async () => {
    try {
      const data = await getAccounts();
      setAccounts(data);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHealth = useCallback(async () => {
    try {
      const h = await getAccountHealth();
      setHealthMap(h);
    } catch {
      /* ignore — health is best-effort */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  // Fetch health on mount and every 5 minutes
  useEffect(() => {
    loadHealth();
    const id = setInterval(loadHealth, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [loadHealth]);

  useEffect(() => {
    fetch("/api/settings")
      .then((r) => r.json())
      .then((d: Record<string, string>) => {
        setMdTokenMasked(d.market_data_token_masked || "");
        setMdClientId(d.market_data_client_id || "");
        setMdNewClientId(d.market_data_client_id || "");
      })
      .catch(() => {});
  }, []);

  const handleSaveMdToken = async () => {
    const body: Record<string, string> = {};
    if (mdNewToken.trim()) body.market_data_token = mdNewToken.trim();
    if (mdNewClientId.trim()) body.market_data_client_id = mdNewClientId.trim();
    if (!Object.keys(body).length) return;
    try {
      await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setMdSaveMsg("Saved ✓");
      setMdNewToken("");
      const d = (await (await fetch("/api/settings")).json()) as Record<
        string,
        string
      >;
      setMdTokenMasked(d.market_data_token_masked || "");
      setMdClientId(d.market_data_client_id || "");
      setTimeout(() => setMdSaveMsg(""), 2500);
    } catch {
      setMdSaveMsg("Error");
      setTimeout(() => setMdSaveMsg(""), 3000);
    }
  };

  const handleModeToggle = async (acc: Account) => {
    const newMode = acc.mode === "PAPER" ? "LIVE" : "PAPER";
    try {
      await patchAccount(acc.client_id, { mode: newMode });
      setAccounts((prev) =>
        prev.map((a) =>
          a.client_id === acc.client_id ? { ...a, mode: newMode } : a,
        ),
      );
    } catch (e: unknown) {
      setError(String(e));
    }
  };

  const handleEnabledToggle = async (acc: Account) => {
    const newEnabled: 0 | 1 = acc.enabled === 1 ? 0 : 1;
    try {
      await patchAccount(acc.client_id, { enabled: newEnabled });
      setAccounts((prev) =>
        prev.map((a) =>
          a.client_id === acc.client_id ? { ...a, enabled: newEnabled } : a,
        ),
      );
    } catch (e: unknown) {
      setError(String(e));
    }
  };

  const handleDelete = async (acc: Account) => {
    if (!window.confirm(`Delete account "${acc.name}" (${acc.client_id})?`))
      return;
    try {
      await deleteAccount(acc.client_id);
      setAccounts((prev) => prev.filter((a) => a.client_id !== acc.client_id));
    } catch (e: unknown) {
      setError(String(e));
    }
  };

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault();
    setAddError("");
    if (!name.trim() || !clientId.trim()) {
      setAddError("Name and Client ID are required.");
      return;
    }
    if (broker === "DHAN" && !accessToken.trim()) {
      setAddError("Access Token is required for Dhan.");
      return;
    }
    if (broker === "ZERODHA" && !apiKey.trim()) {
      setAddError("API Key is required for Zerodha.");
      return;
    }
    if (broker === "ZERODHA" && !apiSecret.trim()) {
      setAddError(
        "API Secret is required for Zerodha (needed for daily token exchange).",
      );
      return;
    }
    setAdding(true);
    try {
      await postAccount({
        name: name.trim(),
        client_id: clientId.trim(),
        access_token: accessToken.trim() || "pending_login",
        broker,
        api_key: broker === "ZERODHA" ? apiKey.trim() : "",
        api_secret: broker === "ZERODHA" ? apiSecret.trim() : "",
      });
      setName("");
      setClientId("");
      setToken("");
      setApiKey("");
      setApiSecret("");
      setAddSuccess(true);
      setTimeout(() => setAddSuccess(false), 2000);
      await load();
    } catch (e: unknown) {
      setAddError(String(e));
    } finally {
      setAdding(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-5 max-w-3xl animate-fade-up">
        <div className="card p-5 space-y-3">
          <div className="skeleton h-4 w-40 rounded" />
          <div className="skeleton h-10 rounded" />
          <div className="skeleton h-8 w-28 rounded" />
        </div>
        {[0, 1].map((i) => (
          <div key={i} className="card p-4 flex items-center gap-4">
            <div className="skeleton w-8 h-8 rounded-lg" />
            <div className="flex-1 space-y-1.5">
              <div className="skeleton h-4 w-28 rounded" />
              <div className="skeleton h-3 w-20 rounded" />
            </div>
            <div className="skeleton h-6 w-16 rounded-lg" />
            <div className="skeleton h-5 w-9 rounded-full" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-8 max-w-3xl animate-fade-up">
      {/* ── Market Data Token ── */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <KeyRound size={14} className="text-[#5A6478]" />
          <h2 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">
            Market Data API Token
          </h2>
        </div>
        <div className="card p-5 space-y-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <span className="text-[#5A6478]">
              Current token:{" "}
              <span className="text-gray-300 font-mono">
                {mdTokenMasked || "NOT SET"}
              </span>
            </span>
            <span className="text-[#3A4255]">|</span>
            <span className="text-[#5A6478]">
              Client ID:{" "}
              <span className="text-gray-300 font-mono">
                {mdClientId || "—"}
              </span>
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <label className="flex flex-col gap-1.5 sm:col-span-2">
              <span className="text-[10px] text-[#5A6478] uppercase tracking-wide font-medium">
                New Token (paste from Dhan)
              </span>
              <input
                type="password"
                value={mdNewToken}
                onChange={(e) => setMdNewToken(e.target.value)}
                placeholder="eyJ…"
                className="input-base"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[10px] text-[#5A6478] uppercase tracking-wide font-medium">
                Client ID
              </span>
              <input
                type="text"
                value={mdNewClientId}
                onChange={(e) => setMdNewClientId(e.target.value)}
                className="input-base"
              />
            </label>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleSaveMdToken}
              className="btn-primary py-1.5 text-xs"
            >
              Update Token
            </button>
            <AnimatePresence>
              {mdSaveMsg && (
                <motion.span
                  initial={{ opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0 }}
                  className={`text-xs font-medium ${mdSaveMsg.startsWith("Saved") ? "text-[#00E676]" : "text-[#FF5252]"}`}
                >
                  {mdSaveMsg}
                </motion.span>
              )}
            </AnimatePresence>
            <span className="text-xs text-[#3A4255] ml-1">
              Refreshes every 5 min.
            </span>
          </div>
        </div>
      </section>

      {/* ── Accounts list ── */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <Shield size={14} className="text-[#5A6478]" />
          <h2 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">
            Accounts{" "}
            <span className="text-[#5A6478] font-normal normal-case">
              ({accounts.length})
            </span>
          </h2>
        </div>

        {error && (
          <div className="flex items-center gap-2 mb-3 px-4 py-2.5 rounded-lg bg-[#FF5252]/10 border border-[#FF5252]/25 text-[#FF5252] text-sm">
            <AlertCircle size={13} /> {error}
          </div>
        )}

        {accounts.length === 0 ? (
          <div className="card flex flex-col items-center justify-center py-10 gap-2">
            <Shield size={24} className="text-[#3A4255]" />
            <p className="text-sm text-[#5A6478]">No accounts configured.</p>
          </div>
        ) : (
          <motion.div
            className="space-y-2"
            initial="hidden"
            animate="visible"
            variants={{ visible: { transition: { staggerChildren: 0.05 } } }}
          >
            {accounts.map((acc) => (
              <motion.div
                key={acc.client_id}
                variants={{
                  hidden: { opacity: 0, y: 6 },
                  visible: { opacity: 1, y: 0 },
                }}
              >
                <AccountRow
                  acc={acc}
                  health={healthMap[acc.client_id]}
                  onModeToggle={handleModeToggle}
                  onEnabledToggle={handleEnabledToggle}
                  onDelete={handleDelete}
                />
              </motion.div>
            ))}
          </motion.div>
        )}
      </section>

      {/* ── Add account ── */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <Plus size={14} className="text-[#5A6478]" />
          <h2 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">
            Add Account
          </h2>
        </div>
        <div className="card p-5">
          <form onSubmit={handleAdd} className="space-y-4">
            {/* Broker selector */}
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-[#5A6478] uppercase tracking-wide font-medium">
                Broker:
              </span>
              {(["DHAN", "ZERODHA"] as const).map((b) => (
                <button
                  key={b}
                  type="button"
                  onClick={() => setBroker(b)}
                  className={`px-4 py-1.5 text-xs rounded-lg border transition-all duration-150 font-bold ${
                    broker === b
                      ? b === "DHAN"
                        ? "bg-blue-500/15 border-blue-500/40 text-blue-400"
                        : "bg-purple-500/15 border-purple-500/40 text-purple-400"
                      : "bg-[#0D0F14] border-[#2A3045] text-[#5A6478] hover:border-[#5A6478]"
                  }`}
                >
                  {b}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="flex flex-col gap-1.5">
                <span className="text-[10px] text-[#5A6478] uppercase tracking-wide font-medium">
                  Name
                </span>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="My Account"
                  className="input-base"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[10px] text-[#5A6478] uppercase tracking-wide font-medium">
                  {broker === "ZERODHA" ? "Zerodha User ID" : "Client ID"}
                </span>
                <input
                  type="text"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  placeholder={broker === "ZERODHA" ? "AB1234" : "DHAN123456"}
                  className="input-base font-mono"
                />
              </label>
            </div>

            {broker === "ZERODHA" && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label className="flex flex-col gap-1.5">
                  <span className="text-[10px] text-[#5A6478] uppercase tracking-wide font-medium">
                    API Key{" "}
                    <span className="text-purple-400">
                      (from developers.kite.trade)
                    </span>
                  </span>
                  <input
                    type="text"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="your_kite_api_key"
                    className="input-base font-mono"
                  />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-[10px] text-[#5A6478] uppercase tracking-wide font-medium">
                    API Secret{" "}
                    <span className="text-purple-400">
                      (for daily token exchange)
                    </span>
                  </span>
                  <input
                    type="password"
                    value={apiSecret}
                    onChange={(e) => setApiSecret(e.target.value)}
                    placeholder="your_kite_api_secret"
                    className="input-base font-mono"
                  />
                </label>
              </div>
            )}

            {broker === "DHAN" && (
              <label className="flex flex-col gap-1.5">
                <span className="text-[10px] text-[#5A6478] uppercase tracking-wide font-medium">
                  Access Token
                </span>
                <input
                  type="password"
                  value={accessToken}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="eyJ…"
                  className="input-base font-mono"
                />
              </label>
            )}

            {broker === "ZERODHA" && (
              <div className="rounded-lg bg-purple-500/5 border border-purple-500/20 px-4 py-3">
                <p className="text-xs text-purple-300 font-medium mb-1">
                  How Zerodha Login Works
                </p>
                <ol className="text-[11px] text-[#5A6478] space-y-1 list-decimal list-inside">
                  <li>
                    Enter your API Key, API Secret, and User ID above, then
                    click <b>Add Account</b>
                  </li>
                  <li>
                    Set your <b>Redirect URL</b> in Zerodha developer console
                    to:
                    <br />
                    <code className="text-purple-400 text-[10px]">
                      {window.location.origin}/api/zerodha/callback
                    </code>
                  </li>
                  <li>
                    After adding, click <b>"Login to Zerodha"</b> on the account
                    card — this opens Kite login
                  </li>
                </ol>
              </div>
            )}

            {addError && (
              <p className="text-xs text-[#FF5252] flex items-center gap-1.5">
                <AlertCircle size={11} /> {addError}
              </p>
            )}

            <div className="flex items-center gap-3">
              <button type="submit" disabled={adding} className="btn-primary">
                {adding ? "Adding…" : "Add Account"}
              </button>
              <AnimatePresence>
                {addSuccess && (
                  <motion.span
                    initial={{ opacity: 0, x: -6 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0 }}
                    className="text-xs text-[#00E676] flex items-center gap-1"
                  >
                    <CheckCircle2 size={11} /> Account added
                  </motion.span>
                )}
              </AnimatePresence>
            </div>
          </form>
        </div>
      </section>
    </div>
  );
}
