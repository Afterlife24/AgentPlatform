"use client";

import { ExternalLink, Table2 } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

export interface CsvTableItem {
    table_uuid: string;
    name: string;
    row_count: number;
    column_schema: { name: string; type: string }[];
    processing_status: string;
}

interface CsvTableSelectorProps {
    value: string[];
    onChange: (uuids: string[]) => void;
    csvTables: CsvTableItem[];
    disabled?: boolean;
    label?: string;
    description?: string;
}

export const CsvTableSelector = ({
    value,
    onChange,
    csvTables,
    disabled = false,
    label = "CSV Tables",
    description = "CSV tables this node can query using the query_csv_table tool.",
}: CsvTableSelectorProps) => {
    // Only show completed tables
    const completedTables = useMemo(
        () => csvTables.filter((t) => t.processing_status === "completed"),
        [csvTables]
    );

    const handleToggle = (tableUuid: string, checked: boolean) => {
        if (checked) {
            onChange([...value, tableUuid]);
        } else {
            onChange(value.filter((uuid) => uuid !== tableUuid));
        }
    };

    if (completedTables.length === 0) {
        return (
            <div className="space-y-2">
                <Label>{label}</Label>
                {description && (
                    <Label className="text-xs text-muted-foreground">
                        {description}
                    </Label>
                )}
                <div className="border rounded-md p-4 space-y-3">
                    <div className="text-sm text-muted-foreground text-center">
                        No CSV tables available. Upload a CSV file in the Files page and select &quot;Table&quot; mode.
                    </div>
                    <div className="flex justify-center">
                        <Button variant="outline" size="sm" asChild>
                            <Link href="/files" target="_blank">
                                <ExternalLink className="h-4 w-4 mr-2" />
                                Upload CSV Table
                            </Link>
                        </Button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-2">
            <Label>{label}</Label>
            {description && (
                <Label className="text-xs text-muted-foreground">
                    {description}
                </Label>
            )}
            <div className="border rounded-md max-h-[300px] overflow-y-auto">
                <div className="divide-y">
                    {completedTables.map((table) => (
                        <div
                            key={table.table_uuid}
                            className="flex items-start gap-3 p-3 hover:bg-muted/50 transition-colors"
                        >
                            <Checkbox
                                id={`csv-${table.table_uuid}`}
                                checked={value.includes(table.table_uuid)}
                                onCheckedChange={(checked) =>
                                    handleToggle(table.table_uuid, checked as boolean)
                                }
                                disabled={disabled}
                            />
                            <div className="flex-1 space-y-1">
                                <label
                                    htmlFor={`csv-${table.table_uuid}`}
                                    className="flex items-center gap-2 cursor-pointer"
                                >
                                    <div className="w-8 h-8 rounded-md bg-emerald-500/10 flex items-center justify-center flex-shrink-0">
                                        <Table2 className="w-4 h-4 text-emerald-500" />
                                    </div>
                                    <div className="min-w-0">
                                        <p className="text-sm font-medium truncate">
                                            {table.name}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            {table.row_count} rows
                                            {table.column_schema?.length
                                                ? ` · ${table.column_schema.length} columns`
                                                : ""}
                                        </p>
                                    </div>
                                </label>
                            </div>
                        </div>
                    ))}
                </div>
            </div>
            {value.length > 0 && (
                <p className="text-xs text-muted-foreground">
                    {value.length} table{value.length !== 1 ? "s" : ""} selected
                </p>
            )}
        </div>
    );
};
