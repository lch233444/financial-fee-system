import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { TaskTabs, TaskView } from "../src/TaskTabs";

function Example({ disabled = false }: { disabled?: boolean }) {
  const [task, setTask] = useState<"entry" | "query">("entry");
  return <><TaskTabs label="任务" value={task} onChange={setTask} disabled={disabled} items={[
    { id: "entry", label: "录入" }, { id: "query", label: "查询" },
  ]} /><TaskView id="entry" active={task === "entry"}><label>待保存内容<input /></label></TaskView>
    <TaskView id="query" active={task === "query"}>查询结果</TaskView></>;
}

test("任务切换支持键盘并保留未提交表单，隐藏任务不进入可访问树", () => {
  render(<Example />);
  fireEvent.change(screen.getByLabelText("待保存内容"), { target: { value: "尚未提交" } });
  fireEvent.keyDown(screen.getByRole("tab", { name: "录入" }), { key: "ArrowRight" });
  expect(screen.getByRole("tabpanel").textContent).toBe("查询结果");
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "查询" }));
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.keyDown(document.activeElement!, { key: "Home" });
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("尚未提交");
  expect(screen.getByRole("tab", { name: "录入" }).getAttribute("aria-selected")).toBe("true");
});

test("提交期间不能通过点击或键盘离开当前任务", () => {
  render(<Example disabled />);
  const query = screen.getByRole("tab", { name: "查询" }) as HTMLButtonElement;
  expect(query.disabled).toBe(true);
  fireEvent.click(query);
  fireEvent.keyDown(screen.getByRole("tab", { name: "录入" }), { key: "End" });
  expect(screen.getByRole("tabpanel").getAttribute("id")).toBe("entry-panel");
});
