import { createContext, useContext } from 'react';

import type { DocumentResponseSchema, ToolResponse } from '@/client/types.gen';
import type { RecordingResponseSchema } from '@/client/types.gen';
import type { CsvTableItem } from '@/components/flow/CsvTableSelector';

interface WorkflowContextType {
    saveWorkflow: (updateWorkflowDefinition?: boolean) => Promise<void>;
    documents?: DocumentResponseSchema[];
    allDocuments?: DocumentResponseSchema[];
    csvTables?: CsvTableItem[];
    tools?: ToolResponse[];
    updateTool?: (
        toolUuid: string,
        updater: (tool: ToolResponse) => ToolResponse,
    ) => void;
    recordings?: RecordingResponseSchema[];
    readOnly?: boolean;
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(undefined);

export const WorkflowProvider = WorkflowContext.Provider;

export const useWorkflow = () => {
    const context = useContext(WorkflowContext);
    if (!context) {
        throw new Error('useWorkflow must be used within a WorkflowProvider');
    }
    return context;
};

// Optional hook that doesn't throw if context is not available
export const useWorkflowOptional = () => {
    return useContext(WorkflowContext);
};
