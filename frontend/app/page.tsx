import VoiceAgent from "@/components/VoiceAgent";
import ServiceableLocations from "@/components/ServiceableLocations";

export default function Home() {
  return (
    <main className="max-w-2xl mx-auto px-4 py-10">
      <h1 className="text-2xl font-semibold mb-1">Voice Booking Agent</h1>
      <p className="text-sm text-neutral-400 mb-4">
        Tap the mic and talk naturally — e.g. &quot;I need to move a few things from
        Koramangala to Whitefield tomorrow evening.&quot;
      </p>
      <ServiceableLocations />
      <VoiceAgent />
    </main>
  );
}
