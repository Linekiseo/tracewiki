import { ChevronDown, ChevronRight, File, Folder, FolderOpen } from "lucide-react";
import { memo, useEffect, useMemo, useState } from "react";
import type { CodeFile } from "../../lib/types";

type TreeNode = {
  name: string;
  path: string;
  kind: "directory" | "file";
  file?: CodeFile;
  children: TreeNode[];
};

function makeTree(files: CodeFile[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", kind: "directory", children: [] };
  const directories = new Map<string, TreeNode>([["", root]]);
  for (const file of files) {
    const parts = file.path.split("/").filter(Boolean);
    let parent = root;
    for (let index = 0; index < parts.length; index += 1) {
      const name = parts[index];
      const path = parts.slice(0, index + 1).join("/");
      const isFile = index === parts.length - 1;
      if (isFile) {
        parent.children.push({ name, path, kind: "file", file, children: [] });
        continue;
      }
      let directory = directories.get(path);
      if (!directory) {
        directory = { name, path, kind: "directory", children: [] };
        directories.set(path, directory);
        parent.children.push(directory);
      }
      parent = directory;
    }
  }
  const sort = (nodes: TreeNode[]) => {
    nodes.sort((left, right) => {
      if (left.kind !== right.kind) return left.kind === "directory" ? -1 : 1;
      return left.name.localeCompare(right.name);
    });
    nodes.forEach((node) => sort(node.children));
  };
  sort(root.children);
  return root.children;
}

function FileIcon({ path }: { path: string }) {
  const extension = path.split(".").pop()?.toLowerCase();
  return <File className={`file-icon file-icon--${extension || "text"}`} size={15} />;
}

const CodeTreeNode = memo(function CodeTreeNode({
  node,
  depth,
  selectedPath,
  searchActive,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  selectedPath: string | null;
  searchActive: boolean;
  onSelect: (path: string) => void;
}) {
  const containsSelection = Boolean(
    selectedPath && selectedPath.startsWith(`${node.path}/`),
  );
  const [open, setOpen] = useState(containsSelection);
  useEffect(() => {
    if (containsSelection) setOpen(true);
  }, [containsSelection]);
  if (node.kind === "file") {
    return (
      <button
        className={`code-tree__row ${selectedPath === node.path ? "is-selected" : ""}`}
        style={{ paddingInlineStart: 12 + depth * 16 }}
        onClick={() => onSelect(node.path)}
        title={node.path}
      >
        <span className="code-tree__caret" />
        <FileIcon path={node.path} />
        <span>{node.name}</span>
      </button>
    );
  }
  const expanded = open || searchActive;
  return (
    <div>
      <button
        className="code-tree__row code-tree__row--folder"
        style={{ paddingInlineStart: 12 + depth * 16 }}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="code-tree__caret">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        {expanded ? <FolderOpen size={16} /> : <Folder size={16} />}
        <span>{node.name}</span>
      </button>
      {expanded
        ? node.children.map((child) => (
            <CodeTreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              searchActive={searchActive}
              onSelect={onSelect}
            />
          ))
        : null}
    </div>
  );
});

export function CodeTree({
  files,
  query,
  selectedPath,
  onSelect,
}: {
  files: CodeFile[];
  query: string;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  const normalizedQuery = query.trim().toLowerCase();
  const visibleFiles = useMemo(
    () =>
      normalizedQuery
        ? files.filter((file) => file.path.toLowerCase().includes(normalizedQuery))
        : files,
    [files, normalizedQuery],
  );
  const tree = useMemo(() => makeTree(visibleFiles), [visibleFiles]);
  return (
    <div className="code-tree" aria-label="仓库文件树">
      {tree.map((node) => (
        <CodeTreeNode
          key={node.path}
          node={node}
          depth={0}
          selectedPath={selectedPath}
          searchActive={Boolean(normalizedQuery)}
          onSelect={onSelect}
        />
      ))}
      {!tree.length ? <p className="code-tree__empty">没有匹配的文件</p> : null}
    </div>
  );
}
