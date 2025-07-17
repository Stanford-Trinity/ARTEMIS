#!/usr/bin/env node

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  Tool,
} from "@modelcontextprotocol/sdk/types.js";
import { execSync } from "child_process";
import { writeFileSync, unlinkSync } from "fs";
import { join } from "path";
import dotenv from "dotenv";
import { fileURLToPath } from 'url';
import { dirname } from 'path';

// Get __dirname equivalent for ES modules
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Load environment variables from .env file
dotenv.config({ path: join(__dirname, '..', '.env') });

// Debug: Check if environment variables are loaded
console.error("Environment check - API token present:", !!process.env.BUGCROWD_API_TOKEN);

interface BugcrowdSubmission {
  title: string;
  description: string;
  program_id?: string;
  target_id?: string;
  engagement_id?: string;
  severity?: number;
  bug_url?: string;
  extra_info?: string;
  http_request?: string;
  researcher_email?: string;
  state?: string;
  cvss_string?: string;
  vrt_id?: string;
  custom_fields?: Record<string, any>;
}

class BugcrowdMCPServer {
  private server: Server;

  constructor() {
    this.server = new Server(
      {
        name: "mcp-bugcrowd",
        version: "1.0.0",
      },
      {
        capabilities: {
          tools: {},
        },
      }
    );

    this.setupToolHandlers();
  }

  private setupToolHandlers() {
    this.server.setRequestHandler(ListToolsRequestSchema, async () => {
      return {
        tools: [
          {
            name: "bugcrowd_submit",
            description: "Submit a vulnerability report to Bugcrowd platform",
            inputSchema: {
              type: "object",
              properties: {
                title: {
                  type: "string",
                  description: "Title of the vulnerability report",
                },
                description: {
                  type: "string",
                  description: "Detailed description of the vulnerability",
                },
                program_id: {
                  type: "string",
                  description: "Bugcrowd program ID (optional - uses env var if not provided)",
                },
                target_id: {
                  type: "string",
                  description: "Target ID within the program (optional)",
                },
                engagement_id: {
                  type: "string",
                  description: "Engagement ID within the program (optional - uses env var if not provided)",
                },
                severity: {
                  type: "number",
                  description: "Severity level (1-5, where 5 is highest)",
                },
                bug_url: {
                  type: "string",
                  description: "URL where the vulnerability was found",
                },
                extra_info: {
                  type: "string",
                  description: "Additional information about the vulnerability",
                },
                http_request: {
                  type: "string",
                  description: "HTTP request details demonstrating the vulnerability",
                },
                researcher_email: {
                  type: "string",
                  description: "Email of the researcher submitting the report",
                },
                state: {
                  type: "string",
                  description: "State of the submission (default: 'new')",
                },
                cvss_string: {
                  type: "string",
                  description: "CVSS string for the vulnerability",
                },
                vrt_id: {
                  type: "string",
                  description: "Vulnerability Rating Taxonomy ID",
                },
                custom_fields: {
                  type: "object",
                  description: "Custom fields as key-value pairs",
                },
              },
              required: ["title", "description"],
            },
          } as Tool,
        ],
      };
    });

    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      const { name, arguments: args } = request.params;

      if (name === "bugcrowd_submit") {
        return await this.handleBugcrowdSubmit(args as unknown as BugcrowdSubmission);
      }

      throw new Error(`Unknown tool: ${name}`);
    });
  }

  private async handleBugcrowdSubmit(submission: BugcrowdSubmission) {
    try {
      const apiToken = process.env.BUGCROWD_API_TOKEN;
      if (!apiToken) {
        throw new Error("BUGCROWD_API_TOKEN environment variable is required");
      }

      // Use environment variables as defaults if not provided in submission
      const programId = submission.program_id || process.env.BUGCROWD_PROGRAM_ID;
      const engagementId = submission.engagement_id || process.env.BUGCROWD_ENGAGEMENT_ID;

      if (!programId) {
        throw new Error("program_id is required (either as parameter or BUGCROWD_PROGRAM_ID env var)");
      }

      // Append "(Codex agent)" to the title
      const modifiedTitle = `${submission.title} (Codex agent)`;

      // Prepare submission data in Bugcrowd API format
      const submissionData = {
        data: {
          type: "submission",
          attributes: {
            title: modifiedTitle,
            description: submission.description,
            state: submission.state || "new",
          },
          relationships: {
            program: {
              data: {
                type: "program",
                id: programId,
              },
            },
          },
        },
      };

      // Add optional attributes
      const attributes = submissionData.data.attributes as any;
      const relationships = submissionData.data.relationships as any;

      if (submission.severity !== undefined) {
        attributes.severity = submission.severity;
      }
      if (submission.bug_url) {
        attributes.bug_url = submission.bug_url;
      }
      if (submission.extra_info) {
        attributes.extra_info = submission.extra_info;
      }
      if (submission.http_request) {
        attributes.http_request = submission.http_request;
      }
      if (submission.researcher_email) {
        attributes.researcher_email = submission.researcher_email;
      }
      if (submission.cvss_string) {
        attributes.cvss_string = submission.cvss_string;
      }
      if (submission.vrt_id) {
        attributes.vrt_id = submission.vrt_id;
      }
      if (submission.custom_fields) {
        attributes.custom_fields = submission.custom_fields;
      }

      // Add target relationship if provided
      if (submission.target_id) {
        relationships.target = {
          data: {
            type: "target",
            id: submission.target_id,
          },
        };
      }

      // Add engagement relationship if provided (use env var as default)
      if (engagementId) {
        relationships.engagement = {
          data: {
            type: "engagement",
            id: engagementId,
          },
        };
      }

      // Write payload to temporary file
      const tempFile = join(__dirname, `submission_${Date.now()}.json`);
      writeFileSync(tempFile, JSON.stringify(submissionData, null, 2));

      try {
        // Execute curl command using spawn approach like Trinity
        const curlArgs = [
          '-X', 'POST',
          '-H', `Authorization: Token ${apiToken}`,
          '-H', 'Content-Type: application/vnd.bugcrowd.v4+json',
          '-H', 'Accept: application/vnd.bugcrowd.v4+json',
          '--data', `@${tempFile}`,
          'https://api.bugcrowd.com/submissions'
        ];

        const curlCommand = 'curl ' + curlArgs.map(arg => 
          arg.includes(' ') ? `"${arg}"` : arg
        ).join(' ');
        
        const result = execSync(curlCommand, { 
          encoding: 'utf8',
          timeout: 30000,
          maxBuffer: 1024 * 1024
        }) as string;

        // Debug: log the raw response
        console.error("Raw API response:", result);
        
        let response;
        try {
          response = JSON.parse(result);
        } catch (parseError) {
          throw new Error(`Invalid JSON response from Bugcrowd API: ${result}`);
        }

        return {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                success: true,
                submission_id: response.data?.id || response.id,
                status: response.data?.attributes?.state || response.state,
                title: modifiedTitle,
                message: "Vulnerability report submitted successfully to Bugcrowd",
                response_data: response,
              }, null, 2),
            },
          ],
        };
      } finally {
        // Clean up temp file
        try {
          unlinkSync(tempFile);
        } catch (e) {
          // Ignore cleanup errors
        }
      }
    } catch (error) {
      let errorMessage = "Failed to submit to Bugcrowd";
      
      if (error instanceof Error) {
        errorMessage = error.message;
        // Log full error for debugging
        console.error("Submission error:", error);
      }

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({
              success: false,
              error: errorMessage,
              message: "Failed to submit vulnerability report to Bugcrowd",
            }, null, 2),
          },
        ],
      };
    }
  }

  async run() {
    try {
      const transport = new StdioServerTransport();
      await this.server.connect(transport);
      console.error("Bugcrowd MCP server started successfully");
    } catch (error) {
      console.error("Failed to start Bugcrowd MCP server:", error);
      process.exit(1);
    }
  }
}

const server = new BugcrowdMCPServer();
server.run().catch((error) => {
  console.error("Server startup failed:", error);
  process.exit(1);
});