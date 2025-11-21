import { useEffect, useMemo, useState } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import {
  ConnectButton,
  UserAudioControl,
} from '@pipecat-ai/voice-ui-kit';

import type { TransportType } from '../../config';
import { AdvicePanel } from './AdvicePanel';
import { TranscriptPanel } from './TranscriptPanel';
import { TransportSelect } from './TransportSelect';

interface AppProps extends PipecatBaseChildProps {
  transportType: TransportType;
  onTransportChange: (type: TransportType) => void;
  availableTransports: TransportType[];
}

type TranscriptEntry = {
  id: string;
  speaker: string;
  text: string;
  timestamp?: string;
};

type Advice = {
  advice: string;
  rationale?: string;
  timestamp?: string;
  utteranceId?: string;
};

const uuid = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

export const App = ({
  client,
  handleConnect,
  handleDisconnect,
  transportType,
  onTransportChange,
  availableTransports,
}: AppProps) => {
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [currentAdvice, setCurrentAdvice] = useState<Advice | null>(null);

  useEffect(() => {
    client?.initDevices();
  }, [client]);

  useEffect(() => {
    if (!client) return;

    const anyClient = client as any;

    const extractRtviMessage = (payload: any) => {
      if (!payload) return null;
      if (payload.label === 'rtvi') return payload;
      if (payload.message?.label === 'rtvi') return payload.message;
      if (payload.data?.label === 'rtvi') return payload.data;
      return null;
    };

    const handleRtviMessage = (raw: any) => {
      const message = extractRtviMessage(raw);
      if (!message) return;

      // User transcription messages
      if (message.type === 'user-transcription' && message.data?.text) {
        if (message.data.final === false) return;
        setTranscript((prev) => [
          ...prev,
          {
            id: message.data.timestamp || uuid(),
            speaker: message.data.user_id || 'customer',
            text: message.data.text,
            timestamp: message.data.timestamp,
          },
        ]);
      }

      // Custom server messages (advice)
      if (message.type === 'server-message' && message.data?.type === 'customer_advice') {
        const payload = message.data;
        setCurrentAdvice({
          advice: payload.advice || '',
          rationale: payload.rationale || '',
          timestamp: payload.timestamp,
          utteranceId: payload.utterance_id || payload.utteranceId || '',
        });
      }
    };

    const subscribe = (evt: string) => {
      anyClient?.on?.(evt, handleRtviMessage);
    };
    const unsubscribe = (evt: string) => {
      anyClient?.off?.(evt, handleRtviMessage);
    };

    ['rtvi-message', 'transport-message', 'message', 'transport-message-urgent'].forEach(subscribe);
    anyClient?.addEventListener?.('message', handleRtviMessage);

    return () => {
      ['rtvi-message', 'transport-message', 'message', 'transport-message-urgent'].forEach(
        unsubscribe
      );
      anyClient?.removeEventListener?.('message', handleRtviMessage);
    };
  }, [client]);

  const showTransportSelector = availableTransports.length > 1;
  const handleConnectWrapper = useMemo(
    () => async () => {
      setTranscript([]);
      setCurrentAdvice(null);
      await handleConnect();
    },
    [handleConnect]
  );

  const handleDisconnectWrapper = useMemo(
    () => async () => {
      await handleDisconnect();
    },
    [handleDisconnect]
  );

  return (
    <div className="flex flex-col w-full h-full">
      <div className="flex items-center justify-between gap-4 p-4">
        {showTransportSelector ? (
          <TransportSelect
            transportType={transportType}
            onTransportChange={onTransportChange}
            availableTransports={availableTransports}
          />
        ) : (
          <div /> /* Spacer */
        )}
        <div className="flex items-center gap-4">
          <UserAudioControl size="lg" />
          <ConnectButton
            size="lg"
            onConnect={handleConnectWrapper}
            onDisconnect={handleDisconnectWrapper}
          />
        </div>
      </div>
      <div className="flex-1 overflow-hidden px-4 pb-4">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 h-full">
          <div className="col-span-1 lg:col-span-2 bg-[--pc-surface] border border-[--pc-border] rounded-xl p-4 overflow-hidden">
            <h2 className="text-lg font-semibold mb-3 text-[--pc-foreground]">
              Transcript
            </h2>
            <TranscriptPanel transcript={transcript} />
          </div>
          <div className="col-span-1 bg-[--pc-surface] border border-[--pc-border] rounded-xl p-4">
            <h2 className="text-lg font-semibold mb-3 text-[--pc-foreground]">
              Current Advice
            </h2>
            <AdvicePanel advice={currentAdvice} />
          </div>
        </div>
      </div>
    </div>
  );
};
