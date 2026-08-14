import PhoneScreen from '../components/PhoneScreen';

export default function GamePlaceholder() {
  return (
    <PhoneScreen title="Savings Quest">
      <div className="flex flex-col items-center text-center gap-4 mt-16">
        <div className="w-24 h-24 rounded-2xl bg-primary-container/15 flex items-center justify-center text-5xl">
          🎮
        </div>
        <h2 className="font-heading font-bold text-lg text-primary">Coming soon</h2>
        <p className="text-on-surface-variant text-[13.5px] max-w-xs">
          Savings Quest is a decorative Easter-egg mini-game in the wireframes — not part of the core onboarding
          flow, so it's kept as a lightweight placeholder here.
        </p>
      </div>
    </PhoneScreen>
  );
}
