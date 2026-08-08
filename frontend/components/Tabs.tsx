"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ROUTES = [
  { href: "/", label: "Ask" },
  { href: "/corpus", label: "Corpus" },
  { href: "/evaluation", label: "Evaluation" },
];

export function Tabs() {
  const pathname = usePathname();
  return (
    <nav className="tabs">
      {ROUTES.map((route) => (
        <Link key={route.href} href={route.href} data-active={pathname === route.href}>
          {route.label}
        </Link>
      ))}
    </nav>
  );
}
