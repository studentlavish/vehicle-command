import { FileSpreadsheet, FileText, FileDown } from "lucide-react";

/** Export formats offered on Reports and Vehicle profile. */
export const FORMATS = [
  { key: "xlsx", label: "Excel", icon: FileSpreadsheet, className: "text-success" },
  { key: "pdf", label: "PDF", icon: FileText, className: "text-destructive" },
  { key: "csv", label: "CSV", icon: FileDown, className: "text-info" },
];
