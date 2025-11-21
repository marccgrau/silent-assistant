type TranscriptEntry = {
  id: string;
  speaker: string;
  text: string;
  timestamp?: string;
};

const colorFor = (speaker: string) => {
  if (/agent/i.test(speaker)) return 'bg-[--pc-accent]/20 text-[--pc-accent]';
  if (/customer|user/i.test(speaker)) return 'bg-[--pc-primary]/15 text-[--pc-primary]';
  if (/advisor|bot/i.test(speaker)) return 'bg-[--pc-warning]/20 text-[--pc-warning]';
  return 'bg-[--pc-muted]/40 text-[--pc-muted-foreground]';
};

export const TranscriptPanel = ({ transcript }: { transcript: TranscriptEntry[] }) => {
  return (
    <div className="h-full flex flex-col gap-3 overflow-hidden">
      <div className="flex-1 overflow-auto space-y-3 pr-1">
        {transcript.length === 0 ? (
          <div className="text-sm text-[--pc-muted-foreground]">
            Waiting for conversation audio to start...
          </div>
        ) : (
          transcript.map((entry) => (
            <div
              key={entry.id}
              className="border border-[--pc-border] rounded-lg p-3 bg-[--pc-card] flex flex-col gap-1"
            >
              <div className="flex items-center gap-2 text-sm">
                <span
                  className={`px-2 py-1 rounded-full text-xs font-semibold ${colorFor(
                    entry.speaker
                  )}`}
                >
                  {entry.speaker || 'speaker'}
                </span>
                {entry.timestamp ? (
                  <span className="text-[--pc-muted-foreground] text-xs">
                    {new Date(entry.timestamp).toLocaleTimeString()}
                  </span>
                ) : null}
              </div>
              <div className="text-[--pc-foreground] text-sm leading-6">{entry.text}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
