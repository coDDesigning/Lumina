import { legalAPI } from '@/api/legal';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';

export function useLegalPolicies() {
  const { data, status } = useQuery({
    key: queryKeys.legalConfig(),
    fetcher: ({ signal }) => legalAPI.getConfig({ signal }),
    fallbackMessage: 'Could not load the legal policy configuration.',
    staleTime: Infinity,
  });

  return {
    enabled: data?.enabled === true,
    isSettled: status === 'success' || status === 'error',
  };
}
