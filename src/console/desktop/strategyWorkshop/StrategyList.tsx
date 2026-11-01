import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { deleteStrategy, listStrategies, type NamedStrategy } from "@/lib/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { WorkshopState } from "@/console/desktop/strategyWorkshop/WorkshopStates";
import { StrategyEditor } from "@/console/desktop/strategyWorkshop/StrategyEditor";
import { DELETE_ACK } from "@/console/desktop/strategyWorkshop/acknowledgments";
import { workshopErrorText } from "@/console/desktop/strategyWorkshop/errorText";

/**
 * "Strategies" (STW-4b #4864; UX-Konzept #4628, Ablauf 1, part 1 — list and delete).
 *
 * Lists the named strategies (#4629) in the order the engine returns them (creation
 * order since #4863) — no client sort, no highlighted row, no figures (ADR-027 Regel 2).
 * The limit comes from `max_strategien` of the response, not from a client constant.
 *
 * Delete asks first and then goes through the sealed `DELETE /api/strategies/{name}`;
 * the list is reloaded from the engine afterwards, never edited optimistically. A strategy
 * that `assignments` names cannot be deleted here (A1-P6). Settings fills `assignments`
 * from `GET /api/strategy-assignments` (#4891); the engine refuses the same delete with
 * 409 `strategy_assigned` and T48 as `detail`.
 *
 * Create, duplicate and edit belong to the editor (#4873): with `withEditor` the card opens
 * StrategyEditor below the list and reloads the list after a save; without it and without
 * a callback a button does not render. Flag off (`GET /api/strategies` → 404): the card
 * does not render.
 *
 * Texts come from `console.strategyWorkshop` (#4892); a failed delete shows the catalogue
 * text of the engine's error code. The acknowledgment comes from `acknowledgments.ts`.
 */

type Load = "loading" | "hidden" | "error" | "ready";

export interface StrategyListProps {
  /** Strategy name → account label. Rows without an entry show "—". */
  assignments?: Record<string, string>;
  onCreate?: () => void;
  onDuplicate?: (name: string) => void;
  onEdit?: (name: string) => void;
  /** Open the built-in StrategyEditor (#4873) for New / Duplicate / Edit; a given callback
   *  above wins over it. After a save the list reloads from the engine. */
  withEditor?: boolean;
}

type EditorEntry = { mode: "new"; source?: string } | { mode: "edit"; name: string };

export function StrategyList({
  assignments = {},
  onCreate: onCreateProp,
  onDuplicate: onDuplicateProp,
  onEdit: onEditProp,
  withEditor = false,
}: StrategyListProps) {
  const { t } = useTranslation();
  const [load, setLoad] = useState<Load>("loading");
  const [editor, setEditor] = useState<EditorEntry | null>(null);
  // Each entry remounts the editor, so it starts from its own source again.
  const [editorKey, setEditorKey] = useState(0);
  const openEditor = (entry: EditorEntry) => {
    setEditor(entry);
    setEditorKey((k) => k + 1);
  };
  const onCreate = onCreateProp ?? (withEditor ? () => openEditor({ mode: "new" }) : undefined);
  const onDuplicate =
    onDuplicateProp ?? (withEditor ? (name: string) => openEditor({ mode: "new", source: name }) : undefined);
  const onEdit = onEditProp ?? (withEditor ? (name: string) => openEditor({ mode: "edit", name }) : undefined);
  const [strategies, setStrategies] = useState<NamedStrategy[]>([]);
  const [max, setMax] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [assignedHint, setAssignedHint] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    // Promise.resolve(...) so a synchronous throw degrades to the error state instead of
    // crashing the hosting Settings page (pattern of the live account strategy card).
    Promise.resolve()
      .then(() => listStrategies())
      .then(
        (list) => {
          if (!alive) return;
          setStrategies(list.strategien);
          setMax(list.max_strategien);
          setLoad("ready");
        },
        (e: unknown) => {
          if (!alive) return;
          const isNotFound =
            typeof e === "object" &&
            e !== null &&
            "status" in e &&
            (e as { status: unknown }).status === 404;
          setLoad(isNotFound ? "hidden" : "error");
        },
      );
    return () => {
      alive = false;
    };
  }, [attempt]);

  if (load === "hidden") return null;

  const askDelete = (name: string) => {
    setDeleteError(null);
    if (assignments[name]) {
      setAssignedHint(true);
      return;
    }
    setAssignedHint(false);
    setPendingDelete(name);
  };

  const confirmDelete = async (name: string) => {
    const nonce =
      globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    try {
      await deleteStrategy(name, DELETE_ACK, nonce);
      setAttempt((n) => n + 1);
    } catch (e: unknown) {
      setDeleteError(workshopErrorText(t, e));
    }
  };

  const atLimit = strategies.length >= max;

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-3">{t("console.strategyWorkshop.T01")}</div>

      {load === "loading" && <WorkshopState kind="loading" />}

      {load === "error" && (
        <WorkshopState
          kind="error"
          action={t("console.strategyWorkshop.state.error.action")}
          onAction={() => {
            setLoad("loading");
            setAttempt((n) => n + 1);
          }}
        />
      )}

      {load === "ready" && strategies.length === 0 && (
        <WorkshopState
          kind="empty"
          text={t("console.strategyWorkshop.state.empty.text.1")}
          action={onCreate ? t("console.strategyWorkshop.state.empty.action.1") : undefined}
          onAction={onCreate}
        />
      )}

      {load === "ready" && strategies.length > 0 && (
        <>
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-white/45">
                <th scope="col" className="font-medium py-2">
                  {t("console.strategyWorkshop.extra.name")}
                </th>
                <th scope="col" className="font-medium py-2">
                  {t("console.strategyWorkshop.extra.assignedTo")}
                </th>
                <th scope="col" className="py-2">
                  <span className="sr-only">{t("console.strategyWorkshop.extra.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s) => (
                <tr key={s.name} className="border-t border-white/6">
                  <td className="py-2 text-white/90">{s.name}</td>
                  <td className="py-2 text-white/70">{assignments[s.name] ?? "—"}</td>
                  <td className="py-2">
                    <div className="flex justify-end gap-2">
                      {onDuplicate && (
                        <button type="button" className="btn" onClick={() => onDuplicate(s.name)}>
                          {t("console.strategyWorkshop.T04")}
                        </button>
                      )}
                      {onEdit && (
                        <button type="button" className="btn" onClick={() => onEdit(s.name)}>
                          {t("console.strategyWorkshop.T45")}
                        </button>
                      )}
                      <button type="button" className="btn" onClick={() => askDelete(s.name)}>
                        {t("console.strategyWorkshop.T44")}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {assignedHint && (
            <p className="surface-flat mt-3 px-4 py-3 text-[13px] text-white/70" role="status">
              {t("console.strategyWorkshop.T48")}
            </p>
          )}
          {deleteError && (
            <p className="surface-flat mt-3 px-4 py-3 text-[13px] text-white/70" role="status">
              {deleteError}
            </p>
          )}
        </>
      )}

      {load === "ready" && strategies.length > 0 && onCreate && (
        <div className="mt-4">
          <button type="button" className="btn" disabled={atLimit} onClick={onCreate}>
            {t("console.strategyWorkshop.T02")}
          </button>
        </div>
      )}
      {load === "ready" && atLimit && strategies.length > 0 && (
        <p className="surface-flat mt-3 px-4 py-3 text-[13px] text-white/70" role="status">
          {t("console.strategyWorkshop.T08")}
        </p>
      )}

      {editor && (
        <StrategyEditor
          key={editorKey}
          mode={editor.mode}
          strategies={strategies}
          source={editor.mode === "new" ? editor.source : undefined}
          initial={editor.mode === "edit" ? strategies.find((s) => s.name === editor.name) : undefined}
          onSaved={() => setAttempt((n) => n + 1)}
          onCancel={() => setEditor(null)}
        />
      )}

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
      >
        <AlertDialogContent className="max-w-[520px] rounded-[20px] border-white/10 bg-[#1c1c1e] text-white">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("console.strategyWorkshop.T46")}</AlertDialogTitle>
            <p className="text-[13px] text-white/90">{pendingDelete}</p>
            <AlertDialogDescription className="text-[13px] text-white/70">
              {t("console.strategyWorkshop.T47")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="btn">{t("console.strategyWorkshop.extra.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="btn"
              onClick={() => {
                if (pendingDelete) void confirmDelete(pendingDelete);
              }}
            >
              {t("console.strategyWorkshop.T44")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
