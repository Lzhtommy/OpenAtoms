export interface AgentMeta {
  name: string;
  role: string;
  avatar: string;
  color: string;
  skill: string;
  skillZh: string;
}

export const AGENTS: AgentMeta[] = [
  { name: "Mike", role: "Team Leader", avatar: "/avatars/mike.webp", color: "var(--role-mike)", skill: "Team Leader", skillZh: "团队负责人" },
  { name: "Emma", role: "Product Manager", avatar: "/avatars/emma.webp", color: "var(--role-emma)", skill: "Product Manager", skillZh: "产品经理" },
  { name: "Bob", role: "Architect", avatar: "/avatars/bob.webp", color: "var(--role-bob)", skill: "Architect", skillZh: "架构师" },
  { name: "Alex", role: "Engineer", avatar: "/avatars/alex.webp", color: "var(--role-alex)", skill: "Engineer", skillZh: "工程师" },
  { name: "David", role: "Data Analyst", avatar: "/avatars/david.webp", color: "var(--role-david)", skill: "Data Analyst", skillZh: "数据分析师" },
  { name: "Iris", role: "Deep Researcher", avatar: "/avatars/iris.webp", color: "var(--role-iris)", skill: "Deep Researcher", skillZh: "深度研究员" },
  { name: "Sarah", role: "SEO Specialist", avatar: "/avatars/sarah.webp", color: "var(--role-sarah)", skill: "SEO Specialist", skillZh: "SEO 专家" },
  { name: "Adrian", role: "Ads Specialist", avatar: "/avatars/adrian.png", color: "var(--role-adrian)", skill: "Ads Specialist", skillZh: "广告专家" },
];

export function agentByRole(role: string | null | undefined): AgentMeta | undefined {
  return AGENTS.find((a) => a.role === role);
}
export function agentByName(name: string | null | undefined): AgentMeta | undefined {
  return AGENTS.find((a) => a.name === name);
}
export function agentFor(role: string, name?: string) {
  return agentByRole(role) ?? agentByName(name);
}
export const TEAM_ROLES = ["Team Leader", "Product Manager", "Architect", "Engineer", "Data Analyst"];
