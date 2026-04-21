import { Editor } from "@/components/editor/editor";

export default function WorkflowEditorPage({
  params,
}: {
  params: { id: string };
}) {
  return <Editor workflowId={params.id} />;
}
