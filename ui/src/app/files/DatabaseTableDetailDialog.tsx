'use client';

import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  Database,
  Filter,
  Key,
  Plus,
  RefreshCw,
  Table as TableIcon,
  Trash2,
  X,
} from 'lucide-react';
import React, { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

import type { DocumentResponseSchema } from '@/client/types.gen';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

interface DatabaseTableDetailDialogProps {
  document: DocumentResponseSchema | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTableUpdated?: () => void;
}

interface ColumnSchemaItem {
  name: string;
  type: string;
  original_header: string;
  conversion_failed?: boolean;
}

interface TypeReportItem {
  name: string;
  assigned_type: string;
  parse_success_count?: number;
  non_conforming_count?: number;
  sample_values?: string[];
  non_conforming_examples?: string[];
}

interface DbTableDetails {
  id: number;
  document_uuid: string;
  table_name: string;
  source_filename: string;
  state: string;
  row_count: number;
  column_count: number;
  confirmed_primary_key: string | null;
  suggested_primary_key: string | null;
  forced_text_columns: string[] | null;
  failure_reason: string | null;
  column_schema: ColumnSchemaItem[];
  type_report: TypeReportItem[] | null;
  created_at: string;
}

interface RelationshipItem {
  id: number;
  source_table_id: number;
  source_table_name: string;
  source_column: string;
  target_table_id: number;
  target_table_name: string;
  target_column: string;
  cardinality: string;
  containment_ratio: number;
  name_similarity: number;
  status: string;
  orphan_count: number;
  orphan_ratio: number;
  example_orphans: any[];
}

interface ComputedColumnItem {
  id: number;
  column_uuid: string;
  db_table_id: number;
  column_name: string;
  sql_expression: string;
  column_type: string;
  created_at: string;
}

interface TableViewItem {
  id: number;
  view_uuid: string;
  db_table_id: number;
  view_name: string;
  rules: Array<{
    source_column: string;
    operator: string;
    operand: any;
  }>;
  created_at: string;
}

export default function DatabaseTableDetailDialog({
  document,
  open,
  onOpenChange,
  onTableUpdated,
}: DatabaseTableDetailDialogProps) {
  const [loading, setLoading] = useState(false);
  const [tableDetails, setTableDetails] = useState<DbTableDetails | null>(null);
  const [relationships, setRelationships] = useState<RelationshipItem[]>([]);
  const [views, setViews] = useState<TableViewItem[]>([]);
  const [computedColumns, setComputedColumns] = useState<ComputedColumnItem[]>([]);
  const [activeTab, setActiveTab] = useState('schema');

  // Primary key selection
  const [selectedPk, setSelectedPk] = useState<string>('');
  const [savingPk, setSavingPk] = useState(false);

  // Forced text & reparse
  const [forcedCols, setForcedCols] = useState<string[]>([]);
  const [reparsing, setReparsing] = useState(false);

  // Computed Column form
  const [newColName, setNewColName] = useState('');
  const [newColExpr, setNewColExpr] = useState('');
  const [newColType, setNewColType] = useState('text');
  const [addingCol, setAddingCol] = useState(false);

  // View form
  const [newViewName, setNewViewName] = useState('');
  const [newViewCol, setNewViewCol] = useState('');
  const [newViewOp, setNewViewOp] = useState('=');
  const [newViewOperand, setNewViewOperand] = useState('');
  const [addingView, setAddingView] = useState(false);

  // Expanded orphan row view
  const [expandedOrphans, setExpandedOrphans] = useState<Record<number, boolean>>({});

  const fetchDetails = useCallback(async () => {
    if (!document?.document_uuid) return;
    setLoading(true);
    try {
      // 1. Fetch table details
      const tableRes = await fetch(`/api/v1/db-tables/${document.document_uuid}`);
      if (!tableRes.ok) {
        if (tableRes.status === 404) {
          setTableDetails(null);
          return;
        }
        throw new Error('Failed to fetch table details');
      }
      const data: DbTableDetails = await tableRes.json();
      setTableDetails(data);
      setSelectedPk(data.confirmed_primary_key || data.suggested_primary_key || '');
      setForcedCols(data.forced_text_columns || []);

      // 2. Fetch relationships
      const relsRes = await fetch('/api/v1/db-tables/relationships');
      if (relsRes.ok) {
        const relsData: RelationshipItem[] = await relsRes.json();
        // Filter relationships involving this table
        setRelationships(
          relsData.filter(
            (r) =>
              r.source_table_name.toLowerCase() === data.table_name.toLowerCase() ||
              r.target_table_name.toLowerCase() === data.table_name.toLowerCase()
          )
        );
      }

      // 3. Fetch computed columns
      const colsRes = await fetch(`/api/v1/db-tables/${data.id}/computed-columns`);
      if (colsRes.ok) {
        const colsData = await colsRes.json();
        setComputedColumns(colsData);
      }

      // 4. Fetch views
      const viewsRes = await fetch(`/api/v1/db-tables/${data.id}/views`);
      if (viewsRes.ok) {
        const viewsData = await viewsRes.json();
        setViews(viewsData);
      }
    } catch (err: any) {
      toast.error(err.message || 'Failed loading table details');
    } finally {
      setLoading(false);
    }
  }, [document?.document_uuid]);

  useEffect(() => {
    if (open && document?.document_uuid) {
      fetchDetails();
    }
  }, [open, document?.document_uuid, fetchDetails]);

  // Primary key handler
  const handleSavePk = async () => {
    if (!document?.document_uuid || !selectedPk) return;
    setSavingPk(true);
    try {
      const res = await fetch(`/api/v1/db-tables/${document.document_uuid}/primary-key`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed_primary_key: selectedPk }),
      });
      if (!res.ok) throw new Error('Failed saving primary key');
      toast.success(`Primary key set to "${selectedPk}"`);
      await fetchDetails();
      onTableUpdated?.();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setSavingPk(false);
    }
  };

  // Reparse handler
  const handleReparse = async () => {
    if (!document?.document_uuid) return;
    setReparsing(true);
    try {
      const res = await fetch(`/api/v1/db-tables/${document.document_uuid}/reparse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ forced_text_columns: forcedCols }),
      });
      if (!res.ok) throw new Error('Reparse failed');
      toast.success('Reparse initiated');
      await fetchDetails();
      onTableUpdated?.();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setReparsing(false);
    }
  };

  // Relationship accept/reject
  const handleRelationshipStatus = async (relId: number, action: 'accept' | 'reject') => {
    try {
      const res = await fetch(`/api/v1/db-tables/relationships/${relId}/${action}`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error(`Failed to ${action} relationship`);
      toast.success(`Relationship ${action}ed`);
      await fetchDetails();
      onTableUpdated?.();
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  // Add computed column
  const handleAddComputedColumn = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tableDetails?.id || !newColName || !newColExpr) return;
    setAddingCol(true);
    try {
      const res = await fetch(`/api/v1/db-tables/${tableDetails.id}/computed-columns`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          column_name: newColName,
          sql_expression: newColExpr,
          column_type: newColType,
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed adding computed column');
      }
      toast.success(`Computed column "${newColName}" created`);
      setNewColName('');
      setNewColExpr('');
      await fetchDetails();
      onTableUpdated?.();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setAddingCol(false);
    }
  };

  // Delete computed column
  const handleDeleteComputedColumn = async (colUuid: string) => {
    try {
      const res = await fetch(`/api/v1/db-tables/computed-columns/${colUuid}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error('Failed deleting computed column');
      toast.success('Computed column deleted');
      await fetchDetails();
      onTableUpdated?.();
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  // Add view
  const handleAddView = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tableDetails?.id || !newViewName || !newViewCol) return;
    setAddingView(true);
    try {
      const res = await fetch(`/api/v1/db-tables/${tableDetails.id}/views`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          view_name: newViewName,
          rules: [
            {
              source_column: newViewCol,
              operator: newViewOp,
              operand: newViewOperand,
            },
          ],
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed creating view');
      }
      toast.success(`Table view "${newViewName}" created`);
      setNewViewName('');
      setNewViewOperand('');
      await fetchDetails();
      onTableUpdated?.();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setAddingView(false);
    }
  };

  // Delete view
  const handleDeleteView = async (viewUuid: string) => {
    try {
      const res = await fetch(`/api/v1/db-tables/views/${viewUuid}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error('Failed deleting view');
      toast.success('View deleted');
      await fetchDetails();
      onTableUpdated?.();
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  const getStateBadge = (state: string) => {
    switch (state) {
      case 'loaded':
        return <Badge className="bg-emerald-600 text-white">Loaded</Badge>;
      case 'loaded_degraded':
        return <Badge className="bg-amber-600 text-white">Loaded (Degraded)</Badge>;
      case 'loading':
        return <Badge variant="secondary" className="animate-pulse">Loading</Badge>;
      case 'pending':
        return <Badge variant="outline">Pending</Badge>;
      case 'failed':
        return <Badge variant="destructive">Failed</Badge>;
      default:
        return <Badge variant="outline">{state}</Badge>;
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <Database className="w-5 h-5 text-indigo-600" />
            <DialogTitle className="text-xl">
              {tableDetails ? tableDetails.table_name : document?.filename}
            </DialogTitle>
            {tableDetails && getStateBadge(tableDetails.state)}
          </div>
          <DialogDescription>
            Physical PostgreSQL table schema, primary keys, relationships, views, and computed columns.
          </DialogDescription>
        </DialogHeader>

        {loading && !tableDetails ? (
          <div className="space-y-4 py-6">
            <Skeleton className="h-8 w-64" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        ) : !tableDetails ? (
          <div className="text-center py-12 text-muted-foreground">
            <TableIcon className="w-12 h-12 mx-auto mb-3 opacity-40" />
            <p>Table information is not available or still being provisioned.</p>
          </div>
        ) : (
          <div className="space-y-6">
            {/* Stats Overview */}
            <div className="grid grid-cols-4 gap-3 bg-muted/40 p-3 rounded-lg border text-center">
              <div>
                <span className="text-xs text-muted-foreground block">Rows</span>
                <span className="text-lg font-bold">{tableDetails.row_count.toLocaleString()}</span>
              </div>
              <div>
                <span className="text-xs text-muted-foreground block">Columns</span>
                <span className="text-lg font-bold">{tableDetails.column_count}</span>
              </div>
              <div>
                <span className="text-xs text-muted-foreground block">Relationships</span>
                <span className="text-lg font-bold">{relationships.length}</span>
              </div>
              <div>
                <span className="text-xs text-muted-foreground block">Views / Derivations</span>
                <span className="text-lg font-bold">{views.length + computedColumns.length}</span>
              </div>
            </div>

            {tableDetails.failure_reason && (
              <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm flex gap-2 items-start">
                <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div>
                  <span className="font-semibold">Notice: </span>
                  {tableDetails.failure_reason}
                </div>
              </div>
            )}

            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
              <TabsList className="grid grid-cols-4 w-full">
                <TabsTrigger value="schema" className="gap-1.5">
                  <TableIcon className="w-3.5 h-3.5" /> Schema & PK
                </TabsTrigger>
                <TabsTrigger value="relationships" className="gap-1.5">
                  <Key className="w-3.5 h-3.5" /> Relationships ({relationships.length})
                </TabsTrigger>
                <TabsTrigger value="computed_columns" className="gap-1.5">
                  <Plus className="w-3.5 h-3.5" /> Computed ({computedColumns.length})
                </TabsTrigger>
                <TabsTrigger value="views" className="gap-1.5">
                  <Filter className="w-3.5 h-3.5" /> Views ({views.length})
                </TabsTrigger>
              </TabsList>

              {/* ─── TAB 1: SCHEMA & PRIMARY KEY ────────────────────────── */}
              <TabsContent value="schema" className="space-y-4 mt-4">
                {/* Primary Key selector card */}
                <div className="p-4 border rounded-lg bg-card space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <h4 className="font-medium text-sm flex items-center gap-1.5">
                        <Key className="w-4 h-4 text-amber-500" /> Primary Key Nomination
                      </h4>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Suggested PK: <code className="text-primary font-mono">{tableDetails.suggested_primary_key || 'None detected'}</code>
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Select value={selectedPk} onValueChange={setSelectedPk}>
                        <SelectTrigger className="w-48 h-8 text-xs">
                          <SelectValue placeholder="Select primary key" />
                        </SelectTrigger>
                        <SelectContent>
                          {tableDetails.column_schema.map((col) => (
                            <SelectItem key={col.name} value={col.name} className="text-xs font-mono">
                              {col.name} ({col.type})
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Button
                        size="sm"
                        onClick={handleSavePk}
                        disabled={savingPk || !selectedPk || selectedPk === tableDetails.confirmed_primary_key}
                      >
                        {savingPk ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : 'Confirm PK'}
                      </Button>
                    </div>
                  </div>
                </div>

                {/* Column Table */}
                <div className="border rounded-md overflow-hidden">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-10">Text</TableHead>
                        <TableHead>Column Identifier</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Original CSV Header</TableHead>
                        <TableHead>Sample Values</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {tableDetails.column_schema.map((col) => {
                        const reportItem = tableDetails.type_report?.find((r) => r.name === col.name);
                        const isForced = forcedCols.includes(col.name);

                        return (
                          <TableRow key={col.name}>
                            <TableCell>
                              <input
                                type="checkbox"
                                title="Force column to TEXT"
                                checked={isForced}
                                onChange={(e) => {
                                  if (e.target.checked) {
                                    setForcedCols([...forcedCols, col.name]);
                                  } else {
                                    setForcedCols(forcedCols.filter((c) => c !== col.name));
                                  }
                                }}
                                className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                              />
                            </TableCell>
                            <TableCell className="font-mono text-xs font-semibold">
                              {col.name}
                              {col.name === tableDetails.confirmed_primary_key && (
                                <Badge variant="secondary" className="ml-2 text-[10px] bg-amber-100 text-amber-900 border-amber-300">
                                  PK
                                </Badge>
                              )}
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline" className="font-mono text-[11px] uppercase">
                                {col.type}
                              </Badge>
                              {col.conversion_failed && (
                                <Badge variant="destructive" className="ml-1 text-[10px]">
                                  Fallback Text
                                </Badge>
                              )}
                            </TableCell>
                            <TableCell className="text-xs text-muted-foreground font-mono">
                              {col.original_header || '—'}
                            </TableCell>
                            <TableCell className="text-xs text-muted-foreground truncate max-w-xs font-mono">
                              {reportItem?.sample_values?.slice(0, 3).join(', ') || '—'}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>

                <div className="flex items-center justify-between pt-2">
                  <span className="text-xs text-muted-foreground">
                    Check columns above and click Reparse to force them as TEXT if type inference was too strict.
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleReparse}
                    disabled={reparsing}
                    className="gap-1.5"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${reparsing ? 'animate-spin' : ''}`} />
                    Reparse Table
                  </Button>
                </div>
              </TabsContent>

              {/* ─── TAB 2: RELATIONSHIPS ──────────────────────────────── */}
              <TabsContent value="relationships" className="space-y-4 mt-4">
                {relationships.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground border rounded-lg border-dashed">
                    <Key className="w-8 h-8 mx-auto mb-2 opacity-30" />
                    <p className="text-sm">No relationships detected or configured for this table.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {relationships.map((rel) => {
                      const isCandidate = rel.status === 'candidate';
                      const isAccepted = rel.status === 'accepted';
                      const showOrphans = expandedOrphans[rel.id];

                      return (
                        <div key={rel.id} className="p-4 border rounded-lg bg-card space-y-2">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 font-mono text-sm font-semibold">
                              <span>{rel.source_table_name}.{rel.source_column}</span>
                              <span className="text-muted-foreground font-normal">→</span>
                              <span>{rel.target_table_name}.{rel.target_column}</span>
                              <Badge variant="outline" className="text-[10px] ml-2 uppercase font-sans">
                                {rel.cardinality}
                              </Badge>
                            </div>
                            <div className="flex items-center gap-2">
                              {isAccepted && (
                                <Badge className="bg-emerald-600 text-white text-xs">Accepted</Badge>
                              )}
                              {rel.status === 'rejected' && (
                                <Badge variant="outline" className="text-muted-foreground text-xs">Rejected</Badge>
                              )}
                              {isCandidate && (
                                <>
                                  <Button
                                    size="sm"
                                    variant="default"
                                    className="bg-emerald-600 hover:bg-emerald-700 h-7 text-xs gap-1"
                                    onClick={() => handleRelationshipStatus(rel.id, 'accept')}
                                  >
                                    <Check className="w-3.5 h-3.5" /> Accept
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    className="h-7 text-xs text-destructive hover:text-destructive gap-1"
                                    onClick={() => handleRelationshipStatus(rel.id, 'reject')}
                                  >
                                    <X className="w-3.5 h-3.5" /> Reject
                                  </Button>
                                </>
                              )}
                            </div>
                          </div>

                          <div className="flex items-center gap-6 text-xs text-muted-foreground pt-1">
                            <span>
                              Containment: <strong>{(rel.containment_ratio * 100).toFixed(1)}%</strong>
                            </span>
                            <span>
                              Name Match: <strong>{(rel.name_similarity * 100).toFixed(1)}%</strong>
                            </span>
                            <span>
                              Orphan rows: <strong>{rel.orphan_count}</strong> ({(rel.orphan_ratio * 100).toFixed(1)}%)
                            </span>
                            {rel.orphan_count > 0 && (
                              <button
                                type="button"
                                onClick={() =>
                                  setExpandedOrphans({
                                    ...expandedOrphans,
                                    [rel.id]: !showOrphans,
                                  })
                                }
                                className="text-indigo-600 hover:underline flex items-center gap-0.5 ml-auto"
                              >
                                {showOrphans ? (
                                  <>Hide Orphans <ChevronUp className="w-3 h-3" /></>
                                ) : (
                                  <>View Orphans <ChevronDown className="w-3 h-3" /></>
                                )}
                              </button>
                            )}
                          </div>

                          {showOrphans && rel.example_orphans && rel.example_orphans.length > 0 && (
                            <div className="mt-2 p-2 bg-muted/60 rounded text-xs font-mono space-y-1">
                              <span className="text-[11px] text-muted-foreground font-sans block">
                                Example orphan values in source column:
                              </span>
                              <div className="flex flex-wrap gap-1">
                                {rel.example_orphans.map((ex, idx) => (
                                  <span key={idx} className="bg-background px-1.5 py-0.5 rounded border">
                                    {String(ex)}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </TabsContent>

              {/* ─── TAB 3: COMPUTED COLUMNS ──────────────────────────── */}
              <TabsContent value="computed_columns" className="space-y-4 mt-4">
                <form onSubmit={handleAddComputedColumn} className="p-4 border rounded-lg bg-card space-y-3">
                  <h4 className="font-medium text-sm flex items-center gap-1.5">
                    <Plus className="w-4 h-4 text-primary" /> Add Computed Column
                  </h4>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <Label className="text-xs">Column Name</Label>
                      <Input
                        placeholder="e.g. total_price"
                        value={newColName}
                        onChange={(e) => setNewColName(e.target.value)}
                        className="h-8 text-xs font-mono mt-1"
                        required
                      />
                    </div>
                    <div>
                      <Label className="text-xs">SQL Expression</Label>
                      <Input
                        placeholder="e.g. quantity * unit_price"
                        value={newColExpr}
                        onChange={(e) => setNewColExpr(e.target.value)}
                        className="h-8 text-xs font-mono mt-1"
                        required
                      />
                    </div>
                    <div>
                      <Label className="text-xs">Type</Label>
                      <Select value={newColType} onValueChange={setNewColType}>
                        <SelectTrigger className="h-8 text-xs font-mono mt-1">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="numeric">NUMERIC</SelectItem>
                          <SelectItem value="integer">INTEGER</SelectItem>
                          <SelectItem value="text">TEXT</SelectItem>
                          <SelectItem value="boolean">BOOLEAN</SelectItem>
                          <SelectItem value="date">DATE</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <Button type="submit" size="sm" disabled={addingCol || !newColName || !newColExpr}>
                    {addingCol ? <RefreshCw className="w-3.5 h-3.5 animate-spin mr-1" /> : <Plus className="w-3.5 h-3.5 mr-1" />}
                    Add Column
                  </Button>
                </form>

                {computedColumns.length === 0 ? (
                  <p className="text-xs text-muted-foreground text-center py-6">
                    No computed columns defined for this table.
                  </p>
                ) : (
                  <div className="border rounded-md overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Column Name</TableHead>
                          <TableHead>Expression</TableHead>
                          <TableHead>Type</TableHead>
                          <TableHead className="w-12"></TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {computedColumns.map((col) => (
                          <TableRow key={col.column_uuid}>
                            <TableCell className="font-mono text-xs font-bold">{col.column_name}</TableCell>
                            <TableCell className="font-mono text-xs text-muted-foreground">{col.sql_expression}</TableCell>
                            <TableCell>
                              <Badge variant="outline" className="font-mono text-[10px] uppercase">
                                {col.column_type}
                              </Badge>
                            </TableCell>
                            <TableCell>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => handleDeleteComputedColumn(col.column_uuid)}
                                className="h-7 w-7 text-destructive"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </TabsContent>

              {/* ─── TAB 4: TABLE VIEWS ───────────────────────────────── */}
              <TabsContent value="views" className="space-y-4 mt-4">
                <form onSubmit={handleAddView} className="p-4 border rounded-lg bg-card space-y-3">
                  <h4 className="font-medium text-sm flex items-center gap-1.5">
                    <Filter className="w-4 h-4 text-primary" /> Create Filtered Table View
                  </h4>
                  <div className="grid grid-cols-4 gap-3">
                    <div>
                      <Label className="text-xs">View Name</Label>
                      <Input
                        placeholder="e.g. active_orders"
                        value={newViewName}
                        onChange={(e) => setNewViewName(e.target.value)}
                        className="h-8 text-xs font-mono mt-1"
                        required
                      />
                    </div>
                    <div>
                      <Label className="text-xs">Filter Column</Label>
                      <Select value={newViewCol} onValueChange={setNewViewCol}>
                        <SelectTrigger className="h-8 text-xs font-mono mt-1">
                          <SelectValue placeholder="Column" />
                        </SelectTrigger>
                        <SelectContent>
                          {tableDetails.column_schema.map((col) => (
                            <SelectItem key={col.name} value={col.name} className="text-xs font-mono">
                              {col.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label className="text-xs">Operator</Label>
                      <Select value={newViewOp} onValueChange={setNewViewOp}>
                        <SelectTrigger className="h-8 text-xs font-mono mt-1">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="=">=</SelectItem>
                          <SelectItem value="!=">!=</SelectItem>
                          <SelectItem value=">">&gt;</SelectItem>
                          <SelectItem value="<">&lt;</SelectItem>
                          <SelectItem value=">=">&gt;=</SelectItem>
                          <SelectItem value="<=">&lt;=</SelectItem>
                          <SelectItem value="LIKE">LIKE</SelectItem>
                          <SelectItem value="ILIKE">ILIKE</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label className="text-xs">Operand</Label>
                      <Input
                        placeholder="e.g. active"
                        value={newViewOperand}
                        onChange={(e) => setNewViewOperand(e.target.value)}
                        className="h-8 text-xs font-mono mt-1"
                        required
                      />
                    </div>
                  </div>
                  <Button type="submit" size="sm" disabled={addingView || !newViewName || !newViewCol}>
                    {addingView ? <RefreshCw className="w-3.5 h-3.5 animate-spin mr-1" /> : <Plus className="w-3.5 h-3.5 mr-1" />}
                    Create View
                  </Button>
                </form>

                {views.length === 0 ? (
                  <p className="text-xs text-muted-foreground text-center py-6">
                    No views defined for this table.
                  </p>
                ) : (
                  <div className="border rounded-md overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>View Name</TableHead>
                          <TableHead>Filter Rules</TableHead>
                          <TableHead className="w-12"></TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {views.map((vw) => (
                          <TableRow key={vw.view_uuid}>
                            <TableCell className="font-mono text-xs font-bold">{vw.view_name}</TableCell>
                            <TableCell className="font-mono text-xs text-muted-foreground">
                              {vw.rules.map((r, i) => (
                                <span key={i} className="bg-muted px-1.5 py-0.5 rounded mr-1">
                                  {r.source_column} {r.operator} &apos;{r.operand}&apos;
                                </span>
                              ))}
                            </TableCell>
                            <TableCell>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => handleDeleteView(vw.view_uuid)}
                                className="h-7 w-7 text-destructive"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </TabsContent>
            </Tabs>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
