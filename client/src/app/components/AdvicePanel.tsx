type Advice = {
  advice: string;
  rationale?: string;
  timestamp?: string;
  utteranceId?: string;
};

export const AdvicePanel = ({ advice }: { advice: Advice | null }) => {
  if (!advice) {
    return (
      <div className="text-sm text-[--pc-muted-foreground]">
        Waiting for customer turns to generate advice...
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="border border-[--pc-border] rounded-lg p-3 bg-[--pc-card] space-y-2">
        <div className="flex items-center justify-between text-xs text-[--pc-muted-foreground]">
          <span>For latest customer turn</span>
          {advice.timestamp ? (
            <span>{new Date(advice.timestamp).toLocaleTimeString()}</span>
          ) : null}
        </div>
        <div className="font-semibold text-[--pc-foreground] leading-6">{advice.advice}</div>
        {advice.rationale ? (
          <div className="text-sm text-[--pc-muted-foreground] leading-6">
            Rationale: {advice.rationale}
          </div>
        ) : null}
      </div>
    </div>
  );
};
