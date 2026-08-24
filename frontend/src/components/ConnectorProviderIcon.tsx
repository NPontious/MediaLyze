import { Server } from "lucide-react";
import type { SVGProps } from "react";

import { JellyfinIcon } from "./JellyfinIcon";
import { PlexIcon } from "./PlexIcon";

export function ConnectorProviderIcon({ provider, ...props }: SVGProps<SVGSVGElement> & { provider: string }) {
  switch (provider.toLowerCase()) {
    case "jellyfin":
      return <JellyfinIcon {...props} />;
    case "plex":
      return <PlexIcon {...props} />;
    default:
      return <Server {...props} />;
  }
}
