import { useEffect, useState } from "react";
import { LogOut } from "lucide-react";
import { signOut } from "firebase/auth";
import { auth } from "@/lib/firebase";
import { useAuthState } from "@/components/useAuthState";

interface HeaderProps {
  currentView: string;
  onNavigate: (view: string) => void;
  onChatClick?: () => void;
}

export const Header = ({ currentView, onNavigate }: HeaderProps) => {
  const { user } = useAuthState();
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 60);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const handleLogout = async () => {
    await signOut(auth);
  };

  const navItems = [
    { id: "home",      label: "Home",      protected: false },
    { id: "dashboard", label: "Dashboard", protected: true  },
    { id: "account",   label: "Account",   protected: true  },
  ];

  const avatarLetter = user?.displayName?.[0] ?? user?.email?.[0]?.toUpperCase() ?? "?";

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 px-6 py-3.5 flex justify-between items-center transition-all duration-300 ${
        scrolled ? "topbar-scrolled" : ""
      }`}
    >
      {/* Logo */}
      <button
        onClick={() => onNavigate("home")}
        className="text-sm font-semibold tracking-tight"
        style={{ color: "rgba(255,255,255,0.55)", letterSpacing: "-0.01em" }}
      >
        <strong style={{ color: "rgba(255,255,255,0.85)", fontWeight: 700 }}>AAA</strong>
        <span>gents</span>
      </button>

      {/* Nav */}
      <nav className="flex items-center gap-6">
        {navItems.map((item, i) => (
          <span key={item.id} className="flex items-center gap-6">
            {i > 0 && (
              <span
                className="hidden sm:block w-1 h-1 rounded-full"
                style={{ background: "rgba(255,255,255,0.16)" }}
              />
            )}
            <button
              onClick={() => onNavigate(item.id)}
              className="transition-colors duration-200"
              style={{
                fontSize: "12px",
                fontWeight: 500,
                letterSpacing: "0.01em",
                color: currentView === item.id
                  ? "rgba(255,255,255,0.85)"
                  : item.protected && !user
                    ? "rgba(255,255,255,0.2)"
                    : "rgba(255,255,255,0.3)",
              }}
            >
              {item.label}
            </button>
          </span>
        ))}
      </nav>

      {/* Right side: Sign In or Avatar+Logout */}
      <div className="flex items-center gap-3">
        {user ? (
          <>
            <button
              onClick={handleLogout}
              className="flex items-center gap-1.5 transition-opacity hover:opacity-70"
              style={{ color: "rgba(255,255,255,0.3)" }}
              title={`Signed in as ${user.email}`}
            >
              <LogOut className="w-3.5 h-3.5" strokeWidth={1.5} />
            </button>
            <button
              onClick={() => onNavigate("account")}
              className="w-6 h-6 rounded-full flex items-center justify-center transition-all duration-200"
              style={{
                background: "rgba(255,255,255,0.06)",
                border: "1px solid rgba(255,255,255,0.08)",
                fontSize: "10px",
                fontWeight: 600,
                color: "rgba(255,255,255,0.55)",
              }}
              title="Account"
            >
              {avatarLetter}
            </button>
          </>
        ) : (
          <button
            onClick={() => onNavigate("dashboard")}
            style={{
              fontSize: 12, fontWeight: 600, padding: "5px 12px",
              borderRadius: 8, border: "1px solid rgba(212,168,83,0.3)",
              background: "rgba(212,168,83,0.08)", color: "#d4a853",
              cursor: "pointer", transition: "all 0.2s", letterSpacing: "0.01em",
            }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "rgba(212,168,83,0.14)"; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "rgba(212,168,83,0.08)"; }}
          >
            Sign In
          </button>
        )}
      </div>
    </header>
  );
};
