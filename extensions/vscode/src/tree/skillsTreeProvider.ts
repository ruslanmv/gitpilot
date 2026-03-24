/**
 * Skills & Plugins Tree View Provider
 *
 * Displays available skills and installed plugins.
 */
import * as vscode from 'vscode';
import { GitPilotApiClient, SkillInfo, PluginInfo } from '../api/client';

type TreeNode = CategoryItem | SkillItem | PluginItem;

export class SkillsTreeProvider implements vscode.TreeDataProvider<TreeNode> {
    private _onDidChangeTreeData = new vscode.EventEmitter<TreeNode | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    constructor(private readonly _client: GitPilotApiClient) {}

    refresh(): void { this._onDidChangeTreeData.fire(undefined); }

    getTreeItem(element: TreeNode): vscode.TreeItem { return element; }

    async getChildren(element?: TreeNode): Promise<TreeNode[]> {
        if (!this._client.isConnected) { return []; }

        if (!element) {
            return [
                new CategoryItem('Skills', 'skills'),
                new CategoryItem('Plugins', 'plugins'),
            ];
        }

        if (element instanceof CategoryItem) {
            if (element.category === 'skills') {
                try {
                    const skills = await this._client.listSkills();
                    return skills.map(s => new SkillItem(s));
                } catch { return []; }
            } else {
                try {
                    const plugins = await this._client.listPlugins();
                    return plugins.map(p => new PluginItem(p));
                } catch { return []; }
            }
        }

        return [];
    }
}

class CategoryItem extends vscode.TreeItem {
    constructor(label: string, public readonly category: 'skills' | 'plugins') {
        super(label, vscode.TreeItemCollapsibleState.Expanded);
        this.iconPath = new vscode.ThemeIcon(category === 'skills' ? 'symbol-method' : 'extensions');
        this.contextValue = `category-${category}`;
    }
}

class SkillItem extends vscode.TreeItem {
    constructor(public readonly skill: SkillInfo) {
        super(`/${skill.name}`, vscode.TreeItemCollapsibleState.None);
        this.description = skill.description;
        this.tooltip = `${skill.description}\nSource: ${skill.source}${skill.auto_trigger ? '\nAuto-trigger: yes' : ''}`;
        this.iconPath = new vscode.ThemeIcon(skill.auto_trigger ? 'zap' : 'symbol-method');
        this.contextValue = 'skill';
        this.command = {
            command: 'gitpilot.invokeSkill',
            title: 'Invoke Skill',
            arguments: [skill.name],
        };
    }
}

class PluginItem extends vscode.TreeItem {
    constructor(public readonly plugin: PluginInfo) {
        super(plugin.name, vscode.TreeItemCollapsibleState.None);
        this.description = `v${plugin.version}`;
        this.tooltip = `${plugin.description}\nSkills: ${plugin.skills.length}\nHooks: ${plugin.hooks.length}`;
        this.iconPath = new vscode.ThemeIcon('package');
        this.contextValue = 'plugin';
    }
}
