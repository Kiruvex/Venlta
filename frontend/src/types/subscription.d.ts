export interface SubscriptionType {
  id: string;
  name: string;
  url: string;
  nodeCount: number | null;
  lastUpdate: string | null;
  autoUpdate: boolean;
  updateInterval: number;
  loading: boolean;
  createdAt?: string;
  updatedAt?: string;
}
