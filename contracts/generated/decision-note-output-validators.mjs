// Generated from canonical committed decision-note deletion outcome. Do not edit.
import unicodeLengthModule from 'ajv/dist/runtime/ucs2length.js';
"use strict";
export const isDecisionNoteDeleteCommitted11 = validate1662;
const schema405 = {"type":"object","properties":{"state":{"type":"string","const":"committed"},"operation":{"type":"string","const":"decisionNote.delete"},"receipt":{"$ref":"#/$defs/DecisionNoteReceipt11"}},"required":["state","operation","receipt"],"additionalProperties":false};
const func0 = Object.prototype.hasOwnProperty;
const schema406 = {"type":"object","properties":{"operation_id":{"$ref":"#/$defs/DecisionNoteUuid11"},"workspace_id":{"$ref":"#/$defs/OpaqueId"},"target":{"$ref":"#/$defs/DecisionNoteTarget"},"committed_workspace_revision":{"$ref":"#/$defs/Revision"},"undoable":{"type":"boolean","const":false}},"required":["operation_id","workspace_id","target","committed_workspace_revision","undoable"],"additionalProperties":false,"description":"A body-free receipt. Deletion and the other decision-note changes carry no workspace undo payload."};
const schema407 = {"type":"string","format":"uuid","minLength":36,"maxLength":36,"pattern":"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"};
const formats0 = /^(?:urn:uuid:)?[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const func74 = unicodeLengthModule.default;
const pattern27 = new RegExp("^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$", "u");

function validate1664(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate1664.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(errors === 0){
if(typeof data === "string"){
if(func74(data) > 36){
validate1664.errors = [{instancePath,schemaPath:"#/maxLength",keyword:"maxLength",params:{limit: 36}}];
return false;
}
else {
if(func74(data) < 36){
validate1664.errors = [{instancePath,schemaPath:"#/minLength",keyword:"minLength",params:{limit: 36}}];
return false;
}
else {
if(!pattern27.test(data)){
validate1664.errors = [{instancePath,schemaPath:"#/pattern",keyword:"pattern",params:{pattern: "^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"}}];
return false;
}
else {
if(!(formats0.test(data))){
validate1664.errors = [{instancePath,schemaPath:"#/format",keyword:"format",params:{format: "uuid"}}];
return false;
}
}
}
}
}
else {
validate1664.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
}
validate1664.errors = vErrors;
return errors === 0;
}
validate1664.evaluated = {"dynamicProps":false,"dynamicItems":false};

const schema33 = {"type":"string","minLength":1,"maxLength":256};

function validate84(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate84.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(typeof data === "string"){
if(func74(data) > 256){
validate84.errors = [{instancePath,schemaPath:"#/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data) < 1){
validate84.errors = [{instancePath,schemaPath:"#/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate84.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
validate84.errors = vErrors;
return errors === 0;
}
validate84.evaluated = {"dynamicProps":false,"dynamicItems":false};

const schema408 = {"type":"object","properties":{"kind":{"type":"string","const":"decision_note"},"object_id":{"$ref":"#/$defs/DecisionNoteUuid11"}},"required":["kind","object_id"],"additionalProperties":false};

function validate1667(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate1667.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if((((data.kind === undefined) || (!(func0.call(data, "kind")))) && (missing0 = "kind")) || (((data.object_id === undefined) || (!(func0.call(data, "object_id")))) && (missing0 = "object_id"))){
validate1667.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!((key0 === "kind") || (key0 === "object_id"))){
validate1667.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.kind !== undefined && func0.call(data, "kind")){
let data0 = data.kind;
const _errs2 = errors;
if(typeof data0 !== "string"){
validate1667.errors = [{instancePath:instancePath+"/kind",schemaPath:"#/properties/kind/type",keyword:"type",params:{type: "string"}}];
return false;
}
if("decision_note" !== data0){
validate1667.errors = [{instancePath:instancePath+"/kind",schemaPath:"#/properties/kind/const",keyword:"const",params:{allowedValue: "decision_note"}}];
return false;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.object_id !== undefined && func0.call(data, "object_id")){
const _errs4 = errors;
if(!(validate1664(data.object_id, {instancePath:instancePath+"/object_id",parentData:data,parentDataProperty:"object_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate1664.errors : vErrors.concat(validate1664.errors);
errors = vErrors.length;
}
var valid0 = _errs4 === errors;
}
else {
var valid0 = true;
}
}
}
}
}
else {
validate1667.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate1667.errors = vErrors;
return errors === 0;
}
validate1667.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema36 = {"type":"integer","minimum":0,"maximum":9007199254740991};

function validate99(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate99.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(!(((typeof data == "number") && (!(data % 1) && !isNaN(data))) && (isFinite(data)))){
validate99.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "integer"}}];
return false;
}
if(errors === 0){
if((typeof data == "number") && (isFinite(data))){
if(data > 9007199254740991 || isNaN(data)){
validate99.errors = [{instancePath,schemaPath:"#/maximum",keyword:"maximum",params:{comparison: "<=", limit: 9007199254740991}}];
return false;
}
else {
if(data < 0 || isNaN(data)){
validate99.errors = [{instancePath,schemaPath:"#/minimum",keyword:"minimum",params:{comparison: ">=", limit: 0}}];
return false;
}
}
}
}
validate99.errors = vErrors;
return errors === 0;
}
validate99.evaluated = {"dynamicProps":false,"dynamicItems":false};


function validate1663(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate1663.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if(((((((data.operation_id === undefined) || (!(func0.call(data, "operation_id")))) && (missing0 = "operation_id")) || (((data.workspace_id === undefined) || (!(func0.call(data, "workspace_id")))) && (missing0 = "workspace_id"))) || (((data.target === undefined) || (!(func0.call(data, "target")))) && (missing0 = "target"))) || (((data.committed_workspace_revision === undefined) || (!(func0.call(data, "committed_workspace_revision")))) && (missing0 = "committed_workspace_revision"))) || (((data.undoable === undefined) || (!(func0.call(data, "undoable")))) && (missing0 = "undoable"))){
validate1663.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!(((((key0 === "operation_id") || (key0 === "workspace_id")) || (key0 === "target")) || (key0 === "committed_workspace_revision")) || (key0 === "undoable"))){
validate1663.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.operation_id !== undefined && func0.call(data, "operation_id")){
const _errs2 = errors;
if(!(validate1664(data.operation_id, {instancePath:instancePath+"/operation_id",parentData:data,parentDataProperty:"operation_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate1664.errors : vErrors.concat(validate1664.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.workspace_id !== undefined && func0.call(data, "workspace_id")){
const _errs3 = errors;
if(!(validate84(data.workspace_id, {instancePath:instancePath+"/workspace_id",parentData:data,parentDataProperty:"workspace_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.target !== undefined && func0.call(data, "target")){
const _errs4 = errors;
if(!(validate1667(data.target, {instancePath:instancePath+"/target",parentData:data,parentDataProperty:"target",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate1667.errors : vErrors.concat(validate1667.errors);
errors = vErrors.length;
}
var valid0 = _errs4 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.committed_workspace_revision !== undefined && func0.call(data, "committed_workspace_revision")){
const _errs5 = errors;
if(!(validate99(data.committed_workspace_revision, {instancePath:instancePath+"/committed_workspace_revision",parentData:data,parentDataProperty:"committed_workspace_revision",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate99.errors : vErrors.concat(validate99.errors);
errors = vErrors.length;
}
var valid0 = _errs5 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.undoable !== undefined && func0.call(data, "undoable")){
let data4 = data.undoable;
const _errs6 = errors;
if(typeof data4 !== "boolean"){
validate1663.errors = [{instancePath:instancePath+"/undoable",schemaPath:"#/properties/undoable/type",keyword:"type",params:{type: "boolean"}}];
return false;
}
if(false !== data4){
validate1663.errors = [{instancePath:instancePath+"/undoable",schemaPath:"#/properties/undoable/const",keyword:"const",params:{allowedValue: false}}];
return false;
}
var valid0 = _errs6 === errors;
}
else {
var valid0 = true;
}
}
}
}
}
}
}
}
else {
validate1663.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate1663.errors = vErrors;
return errors === 0;
}
validate1663.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate1662(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate1662.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if(((((data.state === undefined) || (!(func0.call(data, "state")))) && (missing0 = "state")) || (((data.operation === undefined) || (!(func0.call(data, "operation")))) && (missing0 = "operation"))) || (((data.receipt === undefined) || (!(func0.call(data, "receipt")))) && (missing0 = "receipt"))){
validate1662.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!(((key0 === "state") || (key0 === "operation")) || (key0 === "receipt"))){
validate1662.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.state !== undefined && func0.call(data, "state")){
let data0 = data.state;
const _errs2 = errors;
if(typeof data0 !== "string"){
validate1662.errors = [{instancePath:instancePath+"/state",schemaPath:"#/properties/state/type",keyword:"type",params:{type: "string"}}];
return false;
}
if("committed" !== data0){
validate1662.errors = [{instancePath:instancePath+"/state",schemaPath:"#/properties/state/const",keyword:"const",params:{allowedValue: "committed"}}];
return false;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.operation !== undefined && func0.call(data, "operation")){
let data1 = data.operation;
const _errs4 = errors;
if(typeof data1 !== "string"){
validate1662.errors = [{instancePath:instancePath+"/operation",schemaPath:"#/properties/operation/type",keyword:"type",params:{type: "string"}}];
return false;
}
if("decisionNote.delete" !== data1){
validate1662.errors = [{instancePath:instancePath+"/operation",schemaPath:"#/properties/operation/const",keyword:"const",params:{allowedValue: "decisionNote.delete"}}];
return false;
}
var valid0 = _errs4 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.receipt !== undefined && func0.call(data, "receipt")){
const _errs6 = errors;
if(!(validate1663(data.receipt, {instancePath:instancePath+"/receipt",parentData:data,parentDataProperty:"receipt",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate1663.errors : vErrors.concat(validate1663.errors);
errors = vErrors.length;
}
var valid0 = _errs6 === errors;
}
else {
var valid0 = true;
}
}
}
}
}
}
else {
validate1662.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate1662.errors = vErrors;
return errors === 0;
}
validate1662.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};
