import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { Button, Dialog } from "../../components/ui";

export function CreateProjectDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [question, setQuestion] = useState("");
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: async () => {
      const project = await api.projects.create({
        name: name.trim(),
        description: question.trim(),
      });
      if (question.trim()) await api.projects.createTopic(project.id, question.trim());
      return project;
    },
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      setName("");
      setQuestion("");
      onClose();
      navigate(`/p/${encodeURIComponent(project.id)}/overview`);
    },
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="创建科研项目"
      description="先建立研究容器，仓库、文档、实验与 Codex 会话可稍后接入。"
    >
      <form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) mutation.mutate();
        }}
      >
        <label>
          <span>项目名称 <b>必填</b></span>
          <input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：多智能体协同强化学习"
            maxLength={160}
          />
        </label>
        <label>
          <span>首个研究问题 <em>可选</em></span>
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="项目当前最需要回答的问题是什么？"
            rows={3}
            maxLength={4000}
          />
        </label>
        {mutation.error ? <p className="form-error">{mutation.error.message}</p> : null}
        <footer>
          <Button type="button" variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button type="submit" variant="primary" disabled={!name.trim() || mutation.isPending}>
            {mutation.isPending ? "正在创建…" : "创建并进入项目"}
          </Button>
        </footer>
      </form>
    </Dialog>
  );
}
