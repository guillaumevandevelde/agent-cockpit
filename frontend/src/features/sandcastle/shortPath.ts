export function shortPath(p: string): string {
  const parts = p.split('/')
  return parts.slice(-2).join('/')
}
