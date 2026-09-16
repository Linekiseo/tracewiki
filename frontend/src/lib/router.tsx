import {
  Children,
  createContext,
  isValidElement,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactElement,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

export interface Location {
  pathname: string;
  search: string;
  hash: string;
  state: unknown;
  key: string;
}

interface NavigateOptions {
  replace?: boolean;
  state?: unknown;
}

interface RouterValue {
  location: Location;
  navigate: NavigateFunction;
  createHref: (to: string) => string;
}

export interface NavigateFunction {
  (to: string, options?: NavigateOptions): void;
  (delta: number): void;
}

interface MemoryRouterProps {
  children: ReactNode;
  initialEntries?: string[];
  initialIndex?: number;
}

interface HashRouterProps {
  children: ReactNode;
}

export interface RouteProps {
  children?: ReactNode;
  element?: ReactNode;
  index?: boolean;
  path?: string;
}

interface RoutesProps {
  children?: ReactNode;
}

interface LinkProps
  extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> {
  replace?: boolean;
  state?: unknown;
  to: string;
}

interface NavLinkRenderProps {
  isActive: boolean;
}

interface NavLinkProps extends Omit<LinkProps, "className"> {
  className?: string | ((props: NavLinkRenderProps) => string | undefined);
}

type SearchParamsInit =
  | string
  | string[][]
  | Record<string, string>
  | URLSearchParams;

type SetSearchParams = (
  nextInit:
    | SearchParamsInit
    | ((previous: URLSearchParams) => SearchParamsInit),
  options?: NavigateOptions,
) => void;

interface ParsedPath {
  pathname: string;
  search: string;
  hash: string;
}

interface MemoryHistory {
  entries: Location[];
  index: number;
}

interface RouteMatch {
  child: RouteMatch | null;
  element: ReactNode;
  params: Record<string, string>;
}

const RouterContext = createContext<RouterValue | null>(null);
const OutletContext = createContext<ReactNode>(null);
const ParamsContext = createContext<Record<string, string>>({});

let locationSequence = 0;

function nextLocationKey() {
  locationSequence += 1;
  return `local-${locationSequence}`;
}

function normalizePathname(pathname: string) {
  const withLeadingSlash = pathname.startsWith("/")
    ? pathname
    : `/${pathname}`;
  const collapsed = withLeadingSlash.replace(/\/{2,}/g, "/");
  return collapsed.length > 1 ? collapsed.replace(/\/+$/, "") : collapsed;
}

function parsePath(to: string): ParsedPath {
  const value = String(to || "/").trim();
  if (
    !value.startsWith("/") ||
    value.startsWith("//") ||
    /^[a-z][a-z\d+.-]*:/i.test(value)
  ) {
    throw new Error(`Only absolute in-app routes are supported: ${value}`);
  }

  const hashIndex = value.indexOf("#");
  const beforeHash = hashIndex >= 0 ? value.slice(0, hashIndex) : value;
  const hash = hashIndex >= 0 ? value.slice(hashIndex) : "";
  const searchIndex = beforeHash.indexOf("?");
  const pathname =
    searchIndex >= 0 ? beforeHash.slice(0, searchIndex) : beforeHash;
  const search = searchIndex >= 0 ? beforeHash.slice(searchIndex) : "";

  return {
    pathname: normalizePathname(pathname || "/"),
    search: search === "?" ? "" : search,
    hash: hash === "#" ? "" : hash,
  };
}

function createLocation(to: string, state: unknown = null): Location {
  return { ...parsePath(to), state, key: nextLocationKey() };
}

function locationPath(location: Pick<Location, "pathname" | "search" | "hash">) {
  return `${location.pathname}${location.search}${location.hash}`;
}

function readHashLocation() {
  const route = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : window.location.hash;
  return createLocation(route || "/", window.history.state);
}

function useRouter() {
  const value = useContext(RouterContext);
  if (!value) throw new Error("Router hooks must be used inside a router");
  return value;
}

export function HashRouter({ children }: HashRouterProps) {
  const [location, setLocation] = useState<Location>(() => readHashLocation());

  useEffect(() => {
    const updateLocation = () => setLocation(readHashLocation());
    window.addEventListener("hashchange", updateLocation);
    window.addEventListener("popstate", updateLocation);
    return () => {
      window.removeEventListener("hashchange", updateLocation);
      window.removeEventListener("popstate", updateLocation);
    };
  }, []);

  const navigate = useCallback<NavigateFunction>(
    (to: string | number, options?: NavigateOptions) => {
      if (typeof to === "number") {
        window.history.go(to);
        return;
      }
      const next = createLocation(to, options?.state ?? null);
      const browserPath = `${window.location.pathname}${window.location.search}#${locationPath(next)}`;
      if (options?.replace) {
        window.history.replaceState(next.state, "", browserPath);
      } else {
        window.history.pushState(next.state, "", browserPath);
      }
      setLocation(next);
    },
    [],
  );

  const value = useMemo<RouterValue>(
    () => ({
      location,
      navigate,
      createHref: (to) => `#${locationPath(createLocation(to))}`,
    }),
    [location, navigate],
  );

  return (
    <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
  );
}

export function MemoryRouter({
  children,
  initialEntries = ["/"],
  initialIndex,
}: MemoryRouterProps) {
  const [history, setHistory] = useState<MemoryHistory>(() => {
    const entries = (initialEntries.length ? initialEntries : ["/"]).map(
      (entry) => createLocation(entry),
    );
    const requestedIndex = initialIndex ?? entries.length - 1;
    return {
      entries,
      index: Math.max(0, Math.min(requestedIndex, entries.length - 1)),
    };
  });

  const navigate = useCallback<NavigateFunction>(
    (to: string | number, options?: NavigateOptions) => {
      setHistory((current) => {
        if (typeof to === "number") {
          return {
            ...current,
            index: Math.max(
              0,
              Math.min(current.index + to, current.entries.length - 1),
            ),
          };
        }
        const next = createLocation(to, options?.state ?? null);
        if (options?.replace) {
          const entries = [...current.entries];
          entries[current.index] = next;
          return { entries, index: current.index };
        }
        const entries = [...current.entries.slice(0, current.index + 1), next];
        return { entries, index: entries.length - 1 };
      });
    },
    [],
  );

  const location = history.entries[history.index];
  const value = useMemo<RouterValue>(
    () => ({
      location,
      navigate,
      createHref: (to) => locationPath(createLocation(to)),
    }),
    [location, navigate],
  );

  return (
    <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
  );
}

function decodeParam(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function matchPattern(pattern: string, pathname: string) {
  if (pattern === "*") return {};
  const patternSegments = normalizePathname(pattern).split("/").filter(Boolean);
  const pathSegments = normalizePathname(pathname).split("/").filter(Boolean);
  const wildcard = patternSegments.at(-1) === "*";
  if (
    (!wildcard && patternSegments.length !== pathSegments.length) ||
    (wildcard && pathSegments.length < patternSegments.length - 1)
  ) {
    return null;
  }

  const params: Record<string, string> = {};
  for (let index = 0; index < patternSegments.length; index += 1) {
    const expected = patternSegments[index];
    if (expected === "*") return params;
    const actual = pathSegments[index];
    if (expected.startsWith(":")) {
      if (!actual) return null;
      params[expected.slice(1)] = decodeParam(actual);
      continue;
    }
    if (expected !== actual) return null;
  }
  return params;
}

function findRouteMatch(children: ReactNode, pathname: string): RouteMatch | null {
  for (const child of Children.toArray(children)) {
    if (!isValidElement<RouteProps>(child) || child.type !== Route) continue;
    const { element = null, index, path } = child.props;
    if (index) {
      if (normalizePathname(pathname) === "/") {
        return { child: null, element, params: {} };
      }
      continue;
    }
    if (!path) {
      const nested = findRouteMatch(child.props.children, pathname);
      if (nested) {
        return {
          child: nested,
          element,
          params: nested.params,
        };
      }
      continue;
    }
    const params = matchPattern(path, pathname);
    if (params) return { child: null, element, params };
  }
  return null;
}

function MatchedRoute({ match }: { match: RouteMatch }) {
  const child = match.child ? <MatchedRoute match={match.child} /> : null;
  const rendered = match.element ?? child;
  return (
    <ParamsContext.Provider value={match.params}>
      <OutletContext.Provider value={child}>
        {rendered}
      </OutletContext.Provider>
    </ParamsContext.Provider>
  );
}

export function Routes({ children }: RoutesProps) {
  const { location } = useRouter();
  const match = useMemo(
    () => findRouteMatch(children, location.pathname),
    [children, location.pathname],
  );
  return match ? <MatchedRoute match={match} /> : null;
}

export function Route(_props: RouteProps): ReactElement | null {
  return null;
}

export function Outlet() {
  return useContext(OutletContext);
}

export function Navigate({
  replace = false,
  state,
  to,
}: NavigateOptions & { to: string }) {
  const navigate = useNavigate();
  useEffect(() => navigate(to, { replace, state }), [navigate, replace, state, to]);
  return null;
}

export function Link({
  children,
  onClick,
  replace = false,
  state,
  target,
  to,
  ...anchorProps
}: LinkProps) {
  const { createHref, navigate } = useRouter();
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.altKey ||
      event.ctrlKey ||
      event.shiftKey ||
      (target && target !== "_self")
    ) {
      return;
    }
    event.preventDefault();
    navigate(to, { replace, state });
  };

  return (
    <a
      {...anchorProps}
      href={createHref(to)}
      onClick={handleClick}
      target={target}
    >
      {children}
    </a>
  );
}

export function NavLink({ className, to, ...props }: NavLinkProps) {
  const { location } = useRouter();
  const targetPath = parsePath(to).pathname;
  const isActive =
    location.pathname === targetPath ||
    (targetPath !== "/" && location.pathname.startsWith(`${targetPath}/`));
  const resolvedClassName =
    typeof className === "function" ? className({ isActive }) : className;
  return (
    <Link
      {...props}
      aria-current={isActive ? "page" : props["aria-current"]}
      className={resolvedClassName}
      to={to}
    />
  );
}

export function useLocation() {
  return useRouter().location;
}

export function useNavigate() {
  return useRouter().navigate;
}

export function useParams<
  Params extends Record<string, string | undefined> = Record<
    string,
    string | undefined
  >,
>() {
  return useContext(ParamsContext) as Params;
}

export function useSearchParams(): [URLSearchParams, SetSearchParams] {
  const { location, navigate } = useRouter();
  const searchParams = useMemo(
    () => new URLSearchParams(location.search),
    [location.search],
  );
  const setSearchParams = useCallback<SetSearchParams>(
    (nextInit, options) => {
      const base = new URLSearchParams(location.search);
      const resolved =
        typeof nextInit === "function" ? nextInit(base) : nextInit;
      const next = new URLSearchParams(resolved);
      const query = next.toString();
      navigate(
        `${location.pathname}${query ? `?${query}` : ""}${location.hash}`,
        options,
      );
    },
    [location.hash, location.pathname, location.search, navigate],
  );
  return [searchParams, setSearchParams];
}
