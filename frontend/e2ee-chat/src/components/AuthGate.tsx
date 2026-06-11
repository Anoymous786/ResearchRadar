import { useMemo, useState } from "react";
import { useAuth } from "../hooks/useAuth";
import { useMyProfile } from "../hooks/useMyProfile";
import { useE2eeIdentity } from "../hooks/useE2eeIdentity";

export function AuthGate(props: { children: React.ReactNode }) {
  const { supabase, user, loading } = useAuth();
  const { profile, loading: profileLoading } = useMyProfile();
  const { identity, ready: e2eeReady } = useE2eeIdentity();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<"student" | "faculty">("student");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const needsProfile = useMemo(() => !!user && !profile && !profileLoading, [user, profile, profileLoading]);

  async function signIn() {
    setBusy(true);
    setErr(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) setErr(error.message);
  }

  async function signUp() {
    setBusy(true);
    setErr(null);
    const { error } = await supabase.auth.signUp({ email, password });
    setBusy(false);
    if (error) setErr(error.message);
  }

  async function createProfile() {
    if (!user || !identity) return;
    setBusy(true);
    setErr(null);
    const { error } = await supabase.from("profiles").insert({
      id: user.id,
      role,
      display_name: displayName.trim() || email.split("@")[0] || "User",
      e2ee_public_key_b64: identity.publicKeyB64,
    });
    setBusy(false);
    if (error) setErr(error.message);
  }

  if (loading) {
    return (
      <div className="authWrap">
        <div className="card">Loading…</div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="authWrap">
        <div className="card">
          <h2>Chat login</h2>
          <p>Supabase Auth is required for secure RLS + realtime.</p>
          <div className="grid">
            <input className="input" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
            <input
              className="input"
              placeholder="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {err ? <div style={{ color: "#b91c1c", fontSize: ".9rem" }}>{err}</div> : null}
            <div className="row2">
              <button className="btn" onClick={signIn} disabled={busy || !email || !password}>
                Sign in
              </button>
              <button className="btn" onClick={signUp} disabled={busy || !email || !password}>
                Sign up
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (needsProfile || !e2eeReady) {
    return (
      <div className="authWrap">
        <div className="card">
          <h2>Finish setup</h2>
          <p>Create your chat profile and publish your E2EE public key.</p>
          <div className="grid">
            <input
              className="input"
              placeholder="Display name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
            <select className="select" value={role} onChange={(e) => setRole(e.target.value as "student" | "faculty")}>
              <option value="student">Student</option>
              <option value="faculty">Faculty</option>
            </select>
            {err ? <div style={{ color: "#b91c1c", fontSize: ".9rem" }}>{err}</div> : null}
            <button className="btn" onClick={createProfile} disabled={busy || !identity}>
              Create profile
            </button>
          </div>
        </div>
      </div>
    );
  }

  return <>{props.children}</>;
}

