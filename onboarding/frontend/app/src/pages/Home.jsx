import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../context/AppContext';
import { getSessionState } from '../api/client';
import LanguagePickerSheet from '../components/LanguagePickerSheet';
import MicIcon from '../components/MicIcon';

// Matches the stitch_yono_3.0_proto_changes "yono_3.0_home" wireframe.
// The language sheet auto-opens on first visit (LANGUAGE_PICKED_KEY unset) so
// it's the first thing a new user sees, on top of this page underneath --
// same sheet is reachable later via the header's language toggle.
const LANGUAGE_PICKED_KEY = 'yono3.home_language_picked';

export default function Home() {
  const navigate = useNavigate();
  const { applicationId, sessionId, language, patch, reset } = useApp();
  const [query, setQuery] = useState('');
  const [pickerOpen, setPickerOpen] = useState(() => {
    try {
      return !localStorage.getItem(LANGUAGE_PICKED_KEY);
    } catch {
      return false;
    }
  });

  const closePicker = () => {
    try {
      localStorage.setItem(LANGUAGE_PICKED_KEY, '1');
    } catch {
      /* noop */
    }
    setPickerOpen(false);
  };

  // Same behavior Greeting's "Let's get started" used to kick off -- now that
  // Home is the landing route (`/`), this is what "open a new account" means
  // here (navigate('/') would just re-show this same page).
  const startNewApplication = () => {
    reset();
    navigate('/language');
  };

  const resume = async () => {
    if (!sessionId) return startNewApplication();
    try {
      const state = await getSessionState(sessionId);
      if (state.application.status === 'APPROVED') navigate('/success');
      else if (['UNDER_REVIEW', 'ACTION_NEEDED'].includes(state.application.status)) navigate('/status');
      else navigate('/onboarding');
    } catch {
      navigate('/status');
    }
  };

  const askFinGuru = (seed) => navigate('/finguru/chat', seed ? { state: { seed } } : undefined);

  const submitSearch = (e) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (trimmed) askFinGuru(trimmed);
  };

  return (
    <div className="flex flex-col min-h-screen bg-surface pb-24">
      <header className="sticky top-0 z-30 bg-surface/95 backdrop-blur border-b border-surface-highest">
        <div className="flex items-center justify-between px-5 py-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-primary-fixed flex items-center justify-center text-primary font-heading font-bold text-[13px]">
              Y
            </div>
            <h1 className="font-heading font-bold text-[18px] text-primary">Hi there</h1>
          </div>
          <button
            onClick={() => setPickerOpen(true)}
            aria-label="Change language"
            title="Change language"
            className="w-10 h-10 flex items-center justify-center rounded-full text-primary hover:bg-surface-container active:scale-95 transition"
          >
            <GlobeIcon />
          </button>
        </div>
      </header>

      <main className="px-5 py-5 flex flex-col gap-5 overflow-x-hidden">
        {/* Progress / start banner */}
        <button
          onClick={applicationId ? resume : startNewApplication}
          className="text-left bg-gradient-to-r from-primary-fixed to-tertiary-fixed rounded-2xl p-5 shadow-md active:scale-[0.99] transition"
        >
          {applicationId ? (
            <>
              <span className="inline-block bg-surface/80 backdrop-blur-sm px-3 py-1 rounded-full font-heading font-semibold text-[11px] text-on-surface mb-3">
                In progress
              </span>
              <h2 className="font-heading font-bold text-[19px] text-on-surface w-2/3 leading-snug">
                Continue your application →
              </h2>
            </>
          ) : (
            <>
              <span className="inline-block bg-surface/80 backdrop-blur-sm px-3 py-1 rounded-full font-heading font-semibold text-[11px] text-on-surface mb-3">
                Get started
              </span>
              <h2 className="font-heading font-bold text-[19px] text-on-surface w-2/3 leading-snug">
                Open a new YONO account →
              </h2>
            </>
          )}
        </button>

        {/* Quick-links tile row */}
        <div className="flex gap-3 overflow-x-auto -mx-5 px-5 pb-1" style={{ scrollbarWidth: 'none' }}>
          <QuickTile
            label="Play & Earn"
            iconBg="bg-tertiary-fixed"
            iconColor="text-tertiary-container"
            icon={<GameIcon />}
            onClick={() => navigate('/game')}
          />
          <QuickTile
            label="Get Started"
            iconBg="bg-primary-fixed"
            iconColor="text-primary"
            icon={<BoltIcon />}
            onClick={applicationId ? resume : startNewApplication}
          />
          <QuickTile
            label="YONO Bank"
            iconBg="bg-primary-container/20"
            iconColor="text-primary-container"
            icon={<BankIcon />}
            onClick={() => navigate('/status')}
          />
          <QuickTile
            label="My Rewards"
            iconBg="bg-success-container"
            iconColor="text-success"
            icon={<TrophyIcon />}
            onClick={() => navigate('/game')}
          />
          <QuickTile
            label="Offers"
            iconBg="bg-error-container"
            iconColor="text-error"
            icon={<TagIcon />}
            onClick={() => navigate('/finguru')}
          />
        </div>

        {/* Search / ask bar */}
        <form
          onSubmit={submitSearch}
          className="bg-surface-low rounded-full h-14 px-4 flex items-center gap-2 border-2 border-transparent focus-within:border-primary focus-within:bg-surface-lowest transition-colors shadow-sm"
        >
          <SearchIcon />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search or ask a question"
            className="bg-transparent border-none outline-none flex-1 font-body text-[14px] text-on-surface placeholder:text-outline"
          />
          <button
            type="button"
            onClick={() => askFinGuru()}
            aria-label="Ask by voice"
            className="text-primary hover:bg-surface-container rounded-full p-1.5 transition-colors"
          >
            <MicIcon className="w-4 h-4" />
          </button>
        </form>

        {/* Promo carousel */}
        <div className="flex gap-3 overflow-x-auto -mx-5 px-5 pb-1 snap-x snap-mandatory" style={{ scrollbarWidth: 'none' }}>
          <PromoCard
            emoji="🎮"
            title="Play the game, earn rewards"
            body="Join the daily challenge and win points."
            cta="Play Now"
            onClick={() => navigate('/game')}
            solid
          />
          <PromoCard
            emoji="💡"
            title="Ask FinGuru anything"
            body="Savings, tax, loans, government schemes — in your language."
            cta="Ask now"
            onClick={() => navigate('/finguru')}
          />
        </div>
      </main>

      {/* Bottom nav */}
      <nav className="fixed bottom-0 left-1/2 -translate-x-1/2 w-full max-w-[480px] z-40 bg-surface/90 backdrop-blur border-t border-surface-highest">
        <div className="flex justify-around items-center px-2 py-2">
          <NavTab label="Home" active icon={<HomeIcon />} />
          <NavTab label="Game" icon={<GameIcon />} onClick={() => navigate('/game')} />
          <NavTab label="Chat" icon={<ChatIcon />} onClick={() => navigate('/finguru')} />
          <NavTab label="Accounts" icon={<WalletIcon />} onClick={() => navigate('/status')} />
          <NavTab label="Profile" icon={<PersonIcon />} />
        </div>
      </nav>

      <LanguagePickerSheet
        open={pickerOpen}
        value={language}
        onSelect={(code) => patch({ language: code })}
        onClose={closePicker}
      />
    </div>
  );
}

function QuickTile({ label, icon, iconBg, iconColor, onClick }) {
  return (
    <button
      onClick={onClick}
      className="shrink-0 w-[92px] bg-surface-lowest rounded-2xl p-3 flex flex-col items-center justify-center gap-2 shadow-sm active:scale-95 transition-transform border border-transparent hover:border-outline-variant/40"
    >
      <div className={`w-11 h-11 rounded-full flex items-center justify-center ${iconBg} ${iconColor}`}>{icon}</div>
      <span className="font-heading font-semibold text-[11px] text-on-surface text-center leading-tight">{label}</span>
    </button>
  );
}

function PromoCard({ emoji, title, body, cta, onClick, solid }) {
  return (
    <div className="snap-center shrink-0 w-[82vw] max-w-[300px] rounded-2xl overflow-hidden shadow-sm bg-surface-lowest border border-surface-highest">
      <div className="h-28 flex items-center justify-center text-5xl bg-gradient-to-br from-primary-fixed to-tertiary-fixed">
        {emoji}
      </div>
      <div className="p-4">
        <h3 className="font-heading font-bold text-[15px] text-on-surface mb-1">{title}</h3>
        <p className="font-body text-[12.5px] text-on-surface-variant mb-3">{body}</p>
        <button
          onClick={onClick}
          className={`w-full py-2.5 rounded-full font-heading font-bold text-[13px] active:scale-95 transition ${
            solid ? 'bg-primary text-on-primary' : 'border-2 border-primary text-primary'
          }`}
        >
          {cta}
        </button>
      </div>
    </div>
  );
}

function NavTab({ label, icon, active, onClick }) {
  return (
    <button
      onClick={onClick}
      className={`flex flex-col items-center justify-center gap-0.5 px-4 py-1.5 rounded-full active:scale-90 transition-all ${
        active ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant'
      }`}
    >
      {icon}
      <span className="font-heading font-semibold text-[10px]">{label}</span>
    </button>
  );
}

/* --- inline icons (no icon-font dependency) --- */
function GlobeIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
      <path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );
}
function SearchIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" className="text-outline shrink-0">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
      <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
function GameIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M7 7h10a5 5 0 015 5v0a3 3 0 01-5.6 1.5L15 12H9l-1.4 1.5A3 3 0 012 12v0a5 5 0 015-5zm2 3v2H7v2H5v-2H3v-2h2V8h2v2h2zm7.5 1a1.5 1.5 0 100 3 1.5 1.5 0 000-3zm-3-2.5a1 1 0 100 2 1 1 0 000-2z" />
    </svg>
  );
}
function BoltIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M13 2L3 14h7l-1 8 10-12h-7z" />
    </svg>
  );
}
function BankIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2L2 8v2h20V8zM4 11v8H2v2h20v-2h-2v-8h-2v8h-3v-8h-2v8h-3v-8H8v8H6v-8z" />
    </svg>
  );
}
function TrophyIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M18 3h-1V2H7v1H6a3 3 0 00-3 3v1a4 4 0 004 4h.28A6.01 6.01 0 0011 15.9V18H8v2h8v-2h-3v-2.1a6.01 6.01 0 003.72-3.9H17a4 4 0 004-4V6a3 3 0 00-3-3zM5 7V6a1 1 0 011-1v4a2 2 0 01-1-2zm14 0a2 2 0 01-1 2V5a1 1 0 011 1z" />
    </svg>
  );
}
function TagIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M21.4 12.6L13 4.2A2 2 0 0011.6 3.6L4 3.6A1.4 1.4 0 002.6 5v7.6a2 2 0 00.6 1.4l8.4 8.4a2 2 0 002.8 0l6.9-6.9a2 2 0 000-2.9zM7.2 8.4a1.4 1.4 0 110-2.8 1.4 1.4 0 010 2.8z" />
    </svg>
  );
}
function HomeIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 3l9 8h-3v9h-5v-6h-2v6H6v-9H3z" />
    </svg>
  );
}
function ChatIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M4 4h16v12H7l-3 3z" />
    </svg>
  );
}
function WalletIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M3 6a2 2 0 012-2h13v3H5a1 1 0 000 2h14a1 1 0 011 1v9a1 1 0 01-1 1H5a2 2 0 01-2-2zm14 8a1.5 1.5 0 100-3 1.5 1.5 0 000 3z" />
    </svg>
  );
}
function PersonIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 12a5 5 0 100-10 5 5 0 000 10zm0 2c-4.4 0-8 2.2-8 5v2h16v-2c0-2.8-3.6-5-8-5z" />
    </svg>
  );
}
