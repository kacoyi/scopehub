// Generated from canonical market operation outputs. Do not edit.
import unicodeLengthModule from 'ajv/dist/runtime/ucs2length.js';
import formatsModule from 'ajv-formats/dist/formats.js';
"use strict";
export const isInstrumentSearchOutput = validate1653;
const schema77 = {"type":"object","properties":{"instruments":{"type":"array","items":{"$ref":"#/$defs/Instrument"},"maxItems":50}},"required":["instruments"],"additionalProperties":false};
const func0 = Object.prototype.hasOwnProperty;
const schema78 = {"type":"object","properties":{"id":{"$ref":"#/$defs/OpaqueId"},"display_symbol":{"type":"string","minLength":1,"maxLength":256},"name":{"type":"string","minLength":1,"maxLength":256},"asset_class":{"type":"string","minLength":1,"maxLength":256},"venue":{"type":"string","minLength":1,"maxLength":256},"currency":{"type":"string","minLength":1,"maxLength":32},"calendar_id":{"$ref":"#/$defs/OpaqueId"},"timezone":{"type":"string","minLength":1,"maxLength":256},"price_tick":{"anyOf":[{"type":"string","pattern":"^[0-9]+(\\.[0-9]+)?$"},{"type":"null"}]},"quantity_unit":{"type":"string","minLength":1,"maxLength":256},"tradability":{"$ref":"#/$defs/Tradability"},"metadata":{"$ref":"#/$defs/Metadata"},"provider_mappings":{"type":"array","items":{"$ref":"#/$defs/ProviderMapping"},"maxItems":64}},"required":["id","display_symbol","name","asset_class","venue","currency","calendar_id","timezone","price_tick","quantity_unit","tradability","metadata","provider_mappings"],"additionalProperties":false};
const func74 = unicodeLengthModule.default;
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

const schema79 = {"type":"string","enum":["non_tradable","unknown"]};

function validate267(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate267.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(typeof data !== "string"){
validate267.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "string"}}];
return false;
}
if(!((data === "non_tradable") || (data === "unknown"))){
validate267.errors = [{instancePath,schemaPath:"#/enum",keyword:"enum",params:{allowedValues: schema79.enum}}];
return false;
}
validate267.errors = vErrors;
return errors === 0;
}
validate267.evaluated = {"dynamicProps":false,"dynamicItems":false};

const schema60 = {"type":"object","additionalProperties":true};

function validate179(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate179.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
}
else {
validate179.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate179.errors = vErrors;
return errors === 0;
}
validate179.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema80 = {"type":"object","properties":{"provider_id":{"$ref":"#/$defs/OpaqueId"},"provider_symbol":{"type":"string","minLength":1,"maxLength":256}},"required":["provider_id","provider_symbol"],"additionalProperties":false};

function validate270(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate270.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if((((data.provider_id === undefined) || (!(func0.call(data, "provider_id")))) && (missing0 = "provider_id")) || (((data.provider_symbol === undefined) || (!(func0.call(data, "provider_symbol")))) && (missing0 = "provider_symbol"))){
validate270.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!((key0 === "provider_id") || (key0 === "provider_symbol"))){
validate270.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.provider_id !== undefined && func0.call(data, "provider_id")){
const _errs2 = errors;
if(!(validate84(data.provider_id, {instancePath:instancePath+"/provider_id",parentData:data,parentDataProperty:"provider_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.provider_symbol !== undefined && func0.call(data, "provider_symbol")){
let data1 = data.provider_symbol;
const _errs3 = errors;
if(errors === _errs3){
if(typeof data1 === "string"){
if(func74(data1) > 256){
validate270.errors = [{instancePath:instancePath+"/provider_symbol",schemaPath:"#/properties/provider_symbol/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data1) < 1){
validate270.errors = [{instancePath:instancePath+"/provider_symbol",schemaPath:"#/properties/provider_symbol/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate270.errors = [{instancePath:instancePath+"/provider_symbol",schemaPath:"#/properties/provider_symbol/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
}
}
}
}
else {
validate270.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate270.errors = vErrors;
return errors === 0;
}
validate270.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const pattern5 = new RegExp("^[0-9]+(\\.[0-9]+)?$", "u");

function validate264(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate264.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if(((((((((((((((data.id === undefined) || (!(func0.call(data, "id")))) && (missing0 = "id")) || (((data.display_symbol === undefined) || (!(func0.call(data, "display_symbol")))) && (missing0 = "display_symbol"))) || (((data.name === undefined) || (!(func0.call(data, "name")))) && (missing0 = "name"))) || (((data.asset_class === undefined) || (!(func0.call(data, "asset_class")))) && (missing0 = "asset_class"))) || (((data.venue === undefined) || (!(func0.call(data, "venue")))) && (missing0 = "venue"))) || (((data.currency === undefined) || (!(func0.call(data, "currency")))) && (missing0 = "currency"))) || (((data.calendar_id === undefined) || (!(func0.call(data, "calendar_id")))) && (missing0 = "calendar_id"))) || (((data.timezone === undefined) || (!(func0.call(data, "timezone")))) && (missing0 = "timezone"))) || (((data.price_tick === undefined) || (!(func0.call(data, "price_tick")))) && (missing0 = "price_tick"))) || (((data.quantity_unit === undefined) || (!(func0.call(data, "quantity_unit")))) && (missing0 = "quantity_unit"))) || (((data.tradability === undefined) || (!(func0.call(data, "tradability")))) && (missing0 = "tradability"))) || (((data.metadata === undefined) || (!(func0.call(data, "metadata")))) && (missing0 = "metadata"))) || (((data.provider_mappings === undefined) || (!(func0.call(data, "provider_mappings")))) && (missing0 = "provider_mappings"))){
validate264.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!(func0.call(schema78.properties, key0))){
validate264.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.id !== undefined && func0.call(data, "id")){
const _errs2 = errors;
if(!(validate84(data.id, {instancePath:instancePath+"/id",parentData:data,parentDataProperty:"id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.display_symbol !== undefined && func0.call(data, "display_symbol")){
let data1 = data.display_symbol;
const _errs3 = errors;
if(errors === _errs3){
if(typeof data1 === "string"){
if(func74(data1) > 256){
validate264.errors = [{instancePath:instancePath+"/display_symbol",schemaPath:"#/properties/display_symbol/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data1) < 1){
validate264.errors = [{instancePath:instancePath+"/display_symbol",schemaPath:"#/properties/display_symbol/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate264.errors = [{instancePath:instancePath+"/display_symbol",schemaPath:"#/properties/display_symbol/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.name !== undefined && func0.call(data, "name")){
let data2 = data.name;
const _errs5 = errors;
if(errors === _errs5){
if(typeof data2 === "string"){
if(func74(data2) > 256){
validate264.errors = [{instancePath:instancePath+"/name",schemaPath:"#/properties/name/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data2) < 1){
validate264.errors = [{instancePath:instancePath+"/name",schemaPath:"#/properties/name/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate264.errors = [{instancePath:instancePath+"/name",schemaPath:"#/properties/name/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs5 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.asset_class !== undefined && func0.call(data, "asset_class")){
let data3 = data.asset_class;
const _errs7 = errors;
if(errors === _errs7){
if(typeof data3 === "string"){
if(func74(data3) > 256){
validate264.errors = [{instancePath:instancePath+"/asset_class",schemaPath:"#/properties/asset_class/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data3) < 1){
validate264.errors = [{instancePath:instancePath+"/asset_class",schemaPath:"#/properties/asset_class/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate264.errors = [{instancePath:instancePath+"/asset_class",schemaPath:"#/properties/asset_class/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs7 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.venue !== undefined && func0.call(data, "venue")){
let data4 = data.venue;
const _errs9 = errors;
if(errors === _errs9){
if(typeof data4 === "string"){
if(func74(data4) > 256){
validate264.errors = [{instancePath:instancePath+"/venue",schemaPath:"#/properties/venue/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data4) < 1){
validate264.errors = [{instancePath:instancePath+"/venue",schemaPath:"#/properties/venue/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate264.errors = [{instancePath:instancePath+"/venue",schemaPath:"#/properties/venue/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs9 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.currency !== undefined && func0.call(data, "currency")){
let data5 = data.currency;
const _errs11 = errors;
if(errors === _errs11){
if(typeof data5 === "string"){
if(func74(data5) > 32){
validate264.errors = [{instancePath:instancePath+"/currency",schemaPath:"#/properties/currency/maxLength",keyword:"maxLength",params:{limit: 32}}];
return false;
}
else {
if(func74(data5) < 1){
validate264.errors = [{instancePath:instancePath+"/currency",schemaPath:"#/properties/currency/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate264.errors = [{instancePath:instancePath+"/currency",schemaPath:"#/properties/currency/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs11 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.calendar_id !== undefined && func0.call(data, "calendar_id")){
const _errs13 = errors;
if(!(validate84(data.calendar_id, {instancePath:instancePath+"/calendar_id",parentData:data,parentDataProperty:"calendar_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs13 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.timezone !== undefined && func0.call(data, "timezone")){
let data7 = data.timezone;
const _errs14 = errors;
if(errors === _errs14){
if(typeof data7 === "string"){
if(func74(data7) > 256){
validate264.errors = [{instancePath:instancePath+"/timezone",schemaPath:"#/properties/timezone/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data7) < 1){
validate264.errors = [{instancePath:instancePath+"/timezone",schemaPath:"#/properties/timezone/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate264.errors = [{instancePath:instancePath+"/timezone",schemaPath:"#/properties/timezone/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs14 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.price_tick !== undefined && func0.call(data, "price_tick")){
let data8 = data.price_tick;
const _errs16 = errors;
const _errs17 = errors;
let valid1 = false;
const _errs18 = errors;
if(errors === _errs18){
if(typeof data8 === "string"){
if(!pattern5.test(data8)){
const err0 = {instancePath:instancePath+"/price_tick",schemaPath:"#/properties/price_tick/anyOf/0/pattern",keyword:"pattern",params:{pattern: "^[0-9]+(\\.[0-9]+)?$"}};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
}
else {
const err1 = {instancePath:instancePath+"/price_tick",schemaPath:"#/properties/price_tick/anyOf/0/type",keyword:"type",params:{type: "string"}};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
}
var _valid0 = _errs18 === errors;
valid1 = valid1 || _valid0;
const _errs20 = errors;
if(data8 !== null){
const err2 = {instancePath:instancePath+"/price_tick",schemaPath:"#/properties/price_tick/anyOf/1/type",keyword:"type",params:{type: "null"}};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
var _valid0 = _errs20 === errors;
valid1 = valid1 || _valid0;
if(!valid1){
const err3 = {instancePath:instancePath+"/price_tick",schemaPath:"#/properties/price_tick/anyOf",keyword:"anyOf",params:{}};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
validate264.errors = vErrors;
return false;
}
else {
errors = _errs17;
if(vErrors !== null){
if(_errs17){
vErrors.length = _errs17;
}
else {
vErrors = null;
}
}
}
var valid0 = _errs16 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.quantity_unit !== undefined && func0.call(data, "quantity_unit")){
let data9 = data.quantity_unit;
const _errs22 = errors;
if(errors === _errs22){
if(typeof data9 === "string"){
if(func74(data9) > 256){
validate264.errors = [{instancePath:instancePath+"/quantity_unit",schemaPath:"#/properties/quantity_unit/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data9) < 1){
validate264.errors = [{instancePath:instancePath+"/quantity_unit",schemaPath:"#/properties/quantity_unit/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate264.errors = [{instancePath:instancePath+"/quantity_unit",schemaPath:"#/properties/quantity_unit/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs22 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.tradability !== undefined && func0.call(data, "tradability")){
const _errs24 = errors;
if(!(validate267(data.tradability, {instancePath:instancePath+"/tradability",parentData:data,parentDataProperty:"tradability",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate267.errors : vErrors.concat(validate267.errors);
errors = vErrors.length;
}
var valid0 = _errs24 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.metadata !== undefined && func0.call(data, "metadata")){
const _errs25 = errors;
if(!(validate179(data.metadata, {instancePath:instancePath+"/metadata",parentData:data,parentDataProperty:"metadata",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate179.errors : vErrors.concat(validate179.errors);
errors = vErrors.length;
}
var valid0 = _errs25 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.provider_mappings !== undefined && func0.call(data, "provider_mappings")){
let data12 = data.provider_mappings;
const _errs26 = errors;
if(errors === _errs26){
if(Array.isArray(data12)){
if(data12.length > 64){
validate264.errors = [{instancePath:instancePath+"/provider_mappings",schemaPath:"#/properties/provider_mappings/maxItems",keyword:"maxItems",params:{limit: 64}}];
return false;
}
else {
var valid2 = true;
const len0 = data12.length;
for(let i0=0; i0<len0; i0++){
const _errs28 = errors;
if(!(validate270(data12[i0], {instancePath:instancePath+"/provider_mappings/" + i0,parentData:data12,parentDataProperty:i0,rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate270.errors : vErrors.concat(validate270.errors);
errors = vErrors.length;
}
var valid2 = _errs28 === errors;
if(!valid2){
break;
}
}
}
}
else {
validate264.errors = [{instancePath:instancePath+"/provider_mappings",schemaPath:"#/properties/provider_mappings/type",keyword:"type",params:{type: "array"}}];
return false;
}
}
var valid0 = _errs26 === errors;
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
}
}
}
}
}
}
}
}
else {
validate264.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate264.errors = vErrors;
return errors === 0;
}
validate264.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate1653(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate1653.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if(((data.instruments === undefined) || (!(func0.call(data, "instruments")))) && (missing0 = "instruments")){
validate1653.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!(key0 === "instruments")){
validate1653.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.instruments !== undefined && func0.call(data, "instruments")){
let data0 = data.instruments;
const _errs2 = errors;
if(errors === _errs2){
if(Array.isArray(data0)){
if(data0.length > 50){
validate1653.errors = [{instancePath:instancePath+"/instruments",schemaPath:"#/properties/instruments/maxItems",keyword:"maxItems",params:{limit: 50}}];
return false;
}
else {
var valid1 = true;
const len0 = data0.length;
for(let i0=0; i0<len0; i0++){
const _errs4 = errors;
if(!(validate264(data0[i0], {instancePath:instancePath+"/instruments/" + i0,parentData:data0,parentDataProperty:i0,rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate264.errors : vErrors.concat(validate264.errors);
errors = vErrors.length;
}
var valid1 = _errs4 === errors;
if(!valid1){
break;
}
}
}
}
else {
validate1653.errors = [{instancePath:instancePath+"/instruments",schemaPath:"#/properties/instruments/type",keyword:"type",params:{type: "array"}}];
return false;
}
}
}
}
}
}
else {
validate1653.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate1653.errors = vErrors;
return errors === 0;
}
validate1653.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

export const isMarketHistoryPageOutput = validate1655;
const schema130 = {"type":"object","properties":{"dataset":{"$ref":"#/$defs/DatasetDescriptor"},"bars":{"type":"array","maxItems":100000,"items":{"$ref":"#/$defs/Bar"}},"snapshot":{"$ref":"#/$defs/FrozenSnapshot"},"quality_state":{"$ref":"#/$defs/MarketQualityState"},"stream_position":{"anyOf":[{"$ref":"#/$defs/StreamPosition"},{"type":"null"}]},"next_cursor":{"anyOf":[{"$ref":"#/$defs/HistoryCursor"},{"type":"null"}]},"total_bars":{"type":"integer","minimum":0,"maximum":100000},"offset":{"type":"integer","minimum":0,"maximum":100000}},"required":["dataset","bars","snapshot","quality_state","stream_position","next_cursor","total_bars","offset"],"additionalProperties":false};
const schema82 = {"type":"object","properties":{"id":{"$ref":"#/$defs/Uuid"},"instrument_id":{"$ref":"#/$defs/OpaqueId"},"provider_id":{"$ref":"#/$defs/OpaqueId"},"mode":{"$ref":"#/$defs/DataMode"},"timeframe":{"type":"string","minLength":1,"maxLength":32},"price_adjustment":{"type":"string","minLength":1,"maxLength":256},"timezone":{"type":"string","minLength":1,"maxLength":256},"calendar_id":{"$ref":"#/$defs/OpaqueId"},"quantity_unit":{"type":"string","minLength":1,"maxLength":256},"revision":{"type":"string","minLength":1,"maxLength":256},"quality":{"$ref":"#/$defs/DataQuality"},"provenance":{"$ref":"#/$defs/Metadata"}},"required":["id","instrument_id","provider_id","mode","timeframe","price_adjustment","timezone","calendar_id","quantity_unit","revision","quality","provenance"],"additionalProperties":false};
const schema24 = {"type":"string","format":"uuid"};
const formats0 = /^(?:urn:uuid:)?[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;

function validate58(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate58.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(errors === 0){
if(typeof data === "string"){
if(!(formats0.test(data))){
validate58.errors = [{instancePath,schemaPath:"#/format",keyword:"format",params:{format: "uuid"}}];
return false;
}
}
else {
validate58.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
}
validate58.errors = vErrors;
return errors === 0;
}
validate58.evaluated = {"dynamicProps":false,"dynamicItems":false};

const schema83 = {"type":"string","enum":["simulated","historical","delayed","realtime"]};

function validate280(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate280.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(typeof data !== "string"){
validate280.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "string"}}];
return false;
}
if(!((((data === "simulated") || (data === "historical")) || (data === "delayed")) || (data === "realtime"))){
validate280.errors = [{instancePath,schemaPath:"#/enum",keyword:"enum",params:{allowedValues: schema83.enum}}];
return false;
}
validate280.errors = vErrors;
return errors === 0;
}
validate280.evaluated = {"dynamicProps":false,"dynamicItems":false};

const schema84 = {"type":"string","enum":["fresh","stale","gapped","degraded"]};

function validate283(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate283.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(typeof data !== "string"){
validate283.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "string"}}];
return false;
}
if(!((((data === "fresh") || (data === "stale")) || (data === "gapped")) || (data === "degraded"))){
validate283.errors = [{instancePath,schemaPath:"#/enum",keyword:"enum",params:{allowedValues: schema84.enum}}];
return false;
}
validate283.errors = vErrors;
return errors === 0;
}
validate283.evaluated = {"dynamicProps":false,"dynamicItems":false};


function validate276(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate276.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if((((((((((((((data.id === undefined) || (!(func0.call(data, "id")))) && (missing0 = "id")) || (((data.instrument_id === undefined) || (!(func0.call(data, "instrument_id")))) && (missing0 = "instrument_id"))) || (((data.provider_id === undefined) || (!(func0.call(data, "provider_id")))) && (missing0 = "provider_id"))) || (((data.mode === undefined) || (!(func0.call(data, "mode")))) && (missing0 = "mode"))) || (((data.timeframe === undefined) || (!(func0.call(data, "timeframe")))) && (missing0 = "timeframe"))) || (((data.price_adjustment === undefined) || (!(func0.call(data, "price_adjustment")))) && (missing0 = "price_adjustment"))) || (((data.timezone === undefined) || (!(func0.call(data, "timezone")))) && (missing0 = "timezone"))) || (((data.calendar_id === undefined) || (!(func0.call(data, "calendar_id")))) && (missing0 = "calendar_id"))) || (((data.quantity_unit === undefined) || (!(func0.call(data, "quantity_unit")))) && (missing0 = "quantity_unit"))) || (((data.revision === undefined) || (!(func0.call(data, "revision")))) && (missing0 = "revision"))) || (((data.quality === undefined) || (!(func0.call(data, "quality")))) && (missing0 = "quality"))) || (((data.provenance === undefined) || (!(func0.call(data, "provenance")))) && (missing0 = "provenance"))){
validate276.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!(func0.call(schema82.properties, key0))){
validate276.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.id !== undefined && func0.call(data, "id")){
const _errs2 = errors;
if(!(validate58(data.id, {instancePath:instancePath+"/id",parentData:data,parentDataProperty:"id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate58.errors : vErrors.concat(validate58.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.instrument_id !== undefined && func0.call(data, "instrument_id")){
const _errs3 = errors;
if(!(validate84(data.instrument_id, {instancePath:instancePath+"/instrument_id",parentData:data,parentDataProperty:"instrument_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.provider_id !== undefined && func0.call(data, "provider_id")){
const _errs4 = errors;
if(!(validate84(data.provider_id, {instancePath:instancePath+"/provider_id",parentData:data,parentDataProperty:"provider_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs4 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.mode !== undefined && func0.call(data, "mode")){
const _errs5 = errors;
if(!(validate280(data.mode, {instancePath:instancePath+"/mode",parentData:data,parentDataProperty:"mode",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate280.errors : vErrors.concat(validate280.errors);
errors = vErrors.length;
}
var valid0 = _errs5 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.timeframe !== undefined && func0.call(data, "timeframe")){
let data4 = data.timeframe;
const _errs6 = errors;
if(errors === _errs6){
if(typeof data4 === "string"){
if(func74(data4) > 32){
validate276.errors = [{instancePath:instancePath+"/timeframe",schemaPath:"#/properties/timeframe/maxLength",keyword:"maxLength",params:{limit: 32}}];
return false;
}
else {
if(func74(data4) < 1){
validate276.errors = [{instancePath:instancePath+"/timeframe",schemaPath:"#/properties/timeframe/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate276.errors = [{instancePath:instancePath+"/timeframe",schemaPath:"#/properties/timeframe/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs6 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.price_adjustment !== undefined && func0.call(data, "price_adjustment")){
let data5 = data.price_adjustment;
const _errs8 = errors;
if(errors === _errs8){
if(typeof data5 === "string"){
if(func74(data5) > 256){
validate276.errors = [{instancePath:instancePath+"/price_adjustment",schemaPath:"#/properties/price_adjustment/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data5) < 1){
validate276.errors = [{instancePath:instancePath+"/price_adjustment",schemaPath:"#/properties/price_adjustment/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate276.errors = [{instancePath:instancePath+"/price_adjustment",schemaPath:"#/properties/price_adjustment/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs8 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.timezone !== undefined && func0.call(data, "timezone")){
let data6 = data.timezone;
const _errs10 = errors;
if(errors === _errs10){
if(typeof data6 === "string"){
if(func74(data6) > 256){
validate276.errors = [{instancePath:instancePath+"/timezone",schemaPath:"#/properties/timezone/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data6) < 1){
validate276.errors = [{instancePath:instancePath+"/timezone",schemaPath:"#/properties/timezone/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate276.errors = [{instancePath:instancePath+"/timezone",schemaPath:"#/properties/timezone/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs10 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.calendar_id !== undefined && func0.call(data, "calendar_id")){
const _errs12 = errors;
if(!(validate84(data.calendar_id, {instancePath:instancePath+"/calendar_id",parentData:data,parentDataProperty:"calendar_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs12 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.quantity_unit !== undefined && func0.call(data, "quantity_unit")){
let data8 = data.quantity_unit;
const _errs13 = errors;
if(errors === _errs13){
if(typeof data8 === "string"){
if(func74(data8) > 256){
validate276.errors = [{instancePath:instancePath+"/quantity_unit",schemaPath:"#/properties/quantity_unit/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data8) < 1){
validate276.errors = [{instancePath:instancePath+"/quantity_unit",schemaPath:"#/properties/quantity_unit/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate276.errors = [{instancePath:instancePath+"/quantity_unit",schemaPath:"#/properties/quantity_unit/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs13 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.revision !== undefined && func0.call(data, "revision")){
let data9 = data.revision;
const _errs15 = errors;
if(errors === _errs15){
if(typeof data9 === "string"){
if(func74(data9) > 256){
validate276.errors = [{instancePath:instancePath+"/revision",schemaPath:"#/properties/revision/maxLength",keyword:"maxLength",params:{limit: 256}}];
return false;
}
else {
if(func74(data9) < 1){
validate276.errors = [{instancePath:instancePath+"/revision",schemaPath:"#/properties/revision/minLength",keyword:"minLength",params:{limit: 1}}];
return false;
}
}
}
else {
validate276.errors = [{instancePath:instancePath+"/revision",schemaPath:"#/properties/revision/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
var valid0 = _errs15 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.quality !== undefined && func0.call(data, "quality")){
const _errs17 = errors;
if(!(validate283(data.quality, {instancePath:instancePath+"/quality",parentData:data,parentDataProperty:"quality",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate283.errors : vErrors.concat(validate283.errors);
errors = vErrors.length;
}
var valid0 = _errs17 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.provenance !== undefined && func0.call(data, "provenance")){
const _errs18 = errors;
if(!(validate179(data.provenance, {instancePath:instancePath+"/provenance",parentData:data,parentDataProperty:"provenance",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate179.errors : vErrors.concat(validate179.errors);
errors = vErrors.length;
}
var valid0 = _errs18 === errors;
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
}
}
}
}
}
}
}
else {
validate276.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate276.errors = vErrors;
return errors === 0;
}
validate276.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema85 = {"type":"object","properties":{"time_ms":{"$ref":"#/$defs/Timestamp"},"trading_day":{"type":"string","format":"date"},"open":{"type":"number"},"high":{"type":"number"},"low":{"type":"number"},"close":{"type":"number"},"volume":{"anyOf":[{"type":"number"},{"type":"null"}]},"is_closed":{"type":"boolean"}},"required":["time_ms","trading_day","open","high","low","close","volume","is_closed"],"additionalProperties":false};
const schema25 = {"type":"integer","minimum":-9007199254740991,"maximum":9007199254740991};

function validate60(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate60.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(!(((typeof data == "number") && (!(data % 1) && !isNaN(data))) && (isFinite(data)))){
validate60.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "integer"}}];
return false;
}
if(errors === 0){
if((typeof data == "number") && (isFinite(data))){
if(data > 9007199254740991 || isNaN(data)){
validate60.errors = [{instancePath,schemaPath:"#/maximum",keyword:"maximum",params:{comparison: "<=", limit: 9007199254740991}}];
return false;
}
else {
if(data < -9007199254740991 || isNaN(data)){
validate60.errors = [{instancePath,schemaPath:"#/minimum",keyword:"minimum",params:{comparison: ">=", limit: -9007199254740991}}];
return false;
}
}
}
}
validate60.errors = vErrors;
return errors === 0;
}
validate60.evaluated = {"dynamicProps":false,"dynamicItems":false};

const formats2 = formatsModule.fullFormats.date;

function validate287(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate287.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if((((((((((data.time_ms === undefined) || (!(func0.call(data, "time_ms")))) && (missing0 = "time_ms")) || (((data.trading_day === undefined) || (!(func0.call(data, "trading_day")))) && (missing0 = "trading_day"))) || (((data.open === undefined) || (!(func0.call(data, "open")))) && (missing0 = "open"))) || (((data.high === undefined) || (!(func0.call(data, "high")))) && (missing0 = "high"))) || (((data.low === undefined) || (!(func0.call(data, "low")))) && (missing0 = "low"))) || (((data.close === undefined) || (!(func0.call(data, "close")))) && (missing0 = "close"))) || (((data.volume === undefined) || (!(func0.call(data, "volume")))) && (missing0 = "volume"))) || (((data.is_closed === undefined) || (!(func0.call(data, "is_closed")))) && (missing0 = "is_closed"))){
validate287.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!((((((((key0 === "time_ms") || (key0 === "trading_day")) || (key0 === "open")) || (key0 === "high")) || (key0 === "low")) || (key0 === "close")) || (key0 === "volume")) || (key0 === "is_closed"))){
validate287.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.time_ms !== undefined && func0.call(data, "time_ms")){
const _errs2 = errors;
if(!(validate60(data.time_ms, {instancePath:instancePath+"/time_ms",parentData:data,parentDataProperty:"time_ms",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate60.errors : vErrors.concat(validate60.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.trading_day !== undefined && func0.call(data, "trading_day")){
let data1 = data.trading_day;
const _errs3 = errors;
if(errors === _errs3){
if(errors === _errs3){
if(typeof data1 === "string"){
if(!(formats2.validate(data1))){
validate287.errors = [{instancePath:instancePath+"/trading_day",schemaPath:"#/properties/trading_day/format",keyword:"format",params:{format: "date"}}];
return false;
}
}
else {
validate287.errors = [{instancePath:instancePath+"/trading_day",schemaPath:"#/properties/trading_day/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.open !== undefined && func0.call(data, "open")){
let data2 = data.open;
const _errs5 = errors;
if(!((typeof data2 == "number") && (isFinite(data2)))){
validate287.errors = [{instancePath:instancePath+"/open",schemaPath:"#/properties/open/type",keyword:"type",params:{type: "number"}}];
return false;
}
var valid0 = _errs5 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.high !== undefined && func0.call(data, "high")){
let data3 = data.high;
const _errs7 = errors;
if(!((typeof data3 == "number") && (isFinite(data3)))){
validate287.errors = [{instancePath:instancePath+"/high",schemaPath:"#/properties/high/type",keyword:"type",params:{type: "number"}}];
return false;
}
var valid0 = _errs7 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.low !== undefined && func0.call(data, "low")){
let data4 = data.low;
const _errs9 = errors;
if(!((typeof data4 == "number") && (isFinite(data4)))){
validate287.errors = [{instancePath:instancePath+"/low",schemaPath:"#/properties/low/type",keyword:"type",params:{type: "number"}}];
return false;
}
var valid0 = _errs9 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.close !== undefined && func0.call(data, "close")){
let data5 = data.close;
const _errs11 = errors;
if(!((typeof data5 == "number") && (isFinite(data5)))){
validate287.errors = [{instancePath:instancePath+"/close",schemaPath:"#/properties/close/type",keyword:"type",params:{type: "number"}}];
return false;
}
var valid0 = _errs11 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.volume !== undefined && func0.call(data, "volume")){
let data6 = data.volume;
const _errs13 = errors;
const _errs14 = errors;
let valid1 = false;
const _errs15 = errors;
if(!((typeof data6 == "number") && (isFinite(data6)))){
const err0 = {instancePath:instancePath+"/volume",schemaPath:"#/properties/volume/anyOf/0/type",keyword:"type",params:{type: "number"}};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
var _valid0 = _errs15 === errors;
valid1 = valid1 || _valid0;
const _errs17 = errors;
if(data6 !== null){
const err1 = {instancePath:instancePath+"/volume",schemaPath:"#/properties/volume/anyOf/1/type",keyword:"type",params:{type: "null"}};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
var _valid0 = _errs17 === errors;
valid1 = valid1 || _valid0;
if(!valid1){
const err2 = {instancePath:instancePath+"/volume",schemaPath:"#/properties/volume/anyOf",keyword:"anyOf",params:{}};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
validate287.errors = vErrors;
return false;
}
else {
errors = _errs14;
if(vErrors !== null){
if(_errs14){
vErrors.length = _errs14;
}
else {
vErrors = null;
}
}
}
var valid0 = _errs13 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.is_closed !== undefined && func0.call(data, "is_closed")){
const _errs19 = errors;
if(typeof data.is_closed !== "boolean"){
validate287.errors = [{instancePath:instancePath+"/is_closed",schemaPath:"#/properties/is_closed/type",keyword:"type",params:{type: "boolean"}}];
return false;
}
var valid0 = _errs19 === errors;
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
}
}
}
else {
validate287.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate287.errors = vErrors;
return errors === 0;
}
validate287.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema127 = {"type":"object","properties":{"series":{"$ref":"#/$defs/SeriesKey"},"dataset_id":{"$ref":"#/$defs/Uuid"},"dataset_revision":{"$ref":"#/$defs/OpaqueId"},"from_ms":{"$ref":"#/$defs/Timestamp"},"to_ms":{"$ref":"#/$defs/Timestamp"},"warmup_bars":{"type":"integer","minimum":0,"maximum":100000},"as_of_ms":{"$ref":"#/$defs/Timestamp"},"content_hash":{"$ref":"#/$defs/Hash"}},"required":["series","dataset_id","dataset_revision","from_ms","to_ms","warmup_bars","as_of_ms","content_hash"],"additionalProperties":false};
const schema126 = {"type":"object","properties":{"instrument_id":{"$ref":"#/$defs/OpaqueId"},"provider_id":{"$ref":"#/$defs/OpaqueId"},"timeframe":{"$ref":"#/$defs/OpaqueId"},"price_adjustment":{"$ref":"#/$defs/OpaqueId"},"session_scope":{"$ref":"#/$defs/OpaqueId"},"calendar_id":{"$ref":"#/$defs/OpaqueId"},"quantity_unit":{"$ref":"#/$defs/OpaqueId"}},"required":["instrument_id","provider_id","timeframe","price_adjustment","session_scope","calendar_id","quantity_unit"],"additionalProperties":false};

function validate454(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate454.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if(((((((((data.instrument_id === undefined) || (!(func0.call(data, "instrument_id")))) && (missing0 = "instrument_id")) || (((data.provider_id === undefined) || (!(func0.call(data, "provider_id")))) && (missing0 = "provider_id"))) || (((data.timeframe === undefined) || (!(func0.call(data, "timeframe")))) && (missing0 = "timeframe"))) || (((data.price_adjustment === undefined) || (!(func0.call(data, "price_adjustment")))) && (missing0 = "price_adjustment"))) || (((data.session_scope === undefined) || (!(func0.call(data, "session_scope")))) && (missing0 = "session_scope"))) || (((data.calendar_id === undefined) || (!(func0.call(data, "calendar_id")))) && (missing0 = "calendar_id"))) || (((data.quantity_unit === undefined) || (!(func0.call(data, "quantity_unit")))) && (missing0 = "quantity_unit"))){
validate454.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!(((((((key0 === "instrument_id") || (key0 === "provider_id")) || (key0 === "timeframe")) || (key0 === "price_adjustment")) || (key0 === "session_scope")) || (key0 === "calendar_id")) || (key0 === "quantity_unit"))){
validate454.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.instrument_id !== undefined && func0.call(data, "instrument_id")){
const _errs2 = errors;
if(!(validate84(data.instrument_id, {instancePath:instancePath+"/instrument_id",parentData:data,parentDataProperty:"instrument_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.provider_id !== undefined && func0.call(data, "provider_id")){
const _errs3 = errors;
if(!(validate84(data.provider_id, {instancePath:instancePath+"/provider_id",parentData:data,parentDataProperty:"provider_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.timeframe !== undefined && func0.call(data, "timeframe")){
const _errs4 = errors;
if(!(validate84(data.timeframe, {instancePath:instancePath+"/timeframe",parentData:data,parentDataProperty:"timeframe",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs4 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.price_adjustment !== undefined && func0.call(data, "price_adjustment")){
const _errs5 = errors;
if(!(validate84(data.price_adjustment, {instancePath:instancePath+"/price_adjustment",parentData:data,parentDataProperty:"price_adjustment",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs5 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.session_scope !== undefined && func0.call(data, "session_scope")){
const _errs6 = errors;
if(!(validate84(data.session_scope, {instancePath:instancePath+"/session_scope",parentData:data,parentDataProperty:"session_scope",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs6 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.calendar_id !== undefined && func0.call(data, "calendar_id")){
const _errs7 = errors;
if(!(validate84(data.calendar_id, {instancePath:instancePath+"/calendar_id",parentData:data,parentDataProperty:"calendar_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs7 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.quantity_unit !== undefined && func0.call(data, "quantity_unit")){
const _errs8 = errors;
if(!(validate84(data.quantity_unit, {instancePath:instancePath+"/quantity_unit",parentData:data,parentDataProperty:"quantity_unit",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs8 === errors;
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
}
}
else {
validate454.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate454.errors = vErrors;
return errors === 0;
}
validate454.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema95 = {"type":"string","pattern":"^[0-9a-f]{64}$"};
const pattern6 = new RegExp("^[0-9a-f]{64}$", "u");

function validate336(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate336.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(typeof data === "string"){
if(!pattern6.test(data)){
validate336.errors = [{instancePath,schemaPath:"#/pattern",keyword:"pattern",params:{pattern: "^[0-9a-f]{64}$"}}];
return false;
}
}
else {
validate336.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "string"}}];
return false;
}
}
validate336.errors = vErrors;
return errors === 0;
}
validate336.evaluated = {"dynamicProps":false,"dynamicItems":false};


function validate467(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate467.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if((((((((((data.series === undefined) || (!(func0.call(data, "series")))) && (missing0 = "series")) || (((data.dataset_id === undefined) || (!(func0.call(data, "dataset_id")))) && (missing0 = "dataset_id"))) || (((data.dataset_revision === undefined) || (!(func0.call(data, "dataset_revision")))) && (missing0 = "dataset_revision"))) || (((data.from_ms === undefined) || (!(func0.call(data, "from_ms")))) && (missing0 = "from_ms"))) || (((data.to_ms === undefined) || (!(func0.call(data, "to_ms")))) && (missing0 = "to_ms"))) || (((data.warmup_bars === undefined) || (!(func0.call(data, "warmup_bars")))) && (missing0 = "warmup_bars"))) || (((data.as_of_ms === undefined) || (!(func0.call(data, "as_of_ms")))) && (missing0 = "as_of_ms"))) || (((data.content_hash === undefined) || (!(func0.call(data, "content_hash")))) && (missing0 = "content_hash"))){
validate467.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!((((((((key0 === "series") || (key0 === "dataset_id")) || (key0 === "dataset_revision")) || (key0 === "from_ms")) || (key0 === "to_ms")) || (key0 === "warmup_bars")) || (key0 === "as_of_ms")) || (key0 === "content_hash"))){
validate467.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.series !== undefined && func0.call(data, "series")){
const _errs2 = errors;
if(!(validate454(data.series, {instancePath:instancePath+"/series",parentData:data,parentDataProperty:"series",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate454.errors : vErrors.concat(validate454.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.dataset_id !== undefined && func0.call(data, "dataset_id")){
const _errs3 = errors;
if(!(validate58(data.dataset_id, {instancePath:instancePath+"/dataset_id",parentData:data,parentDataProperty:"dataset_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate58.errors : vErrors.concat(validate58.errors);
errors = vErrors.length;
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.dataset_revision !== undefined && func0.call(data, "dataset_revision")){
const _errs4 = errors;
if(!(validate84(data.dataset_revision, {instancePath:instancePath+"/dataset_revision",parentData:data,parentDataProperty:"dataset_revision",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs4 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.from_ms !== undefined && func0.call(data, "from_ms")){
const _errs5 = errors;
if(!(validate60(data.from_ms, {instancePath:instancePath+"/from_ms",parentData:data,parentDataProperty:"from_ms",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate60.errors : vErrors.concat(validate60.errors);
errors = vErrors.length;
}
var valid0 = _errs5 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.to_ms !== undefined && func0.call(data, "to_ms")){
const _errs6 = errors;
if(!(validate60(data.to_ms, {instancePath:instancePath+"/to_ms",parentData:data,parentDataProperty:"to_ms",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate60.errors : vErrors.concat(validate60.errors);
errors = vErrors.length;
}
var valid0 = _errs6 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.warmup_bars !== undefined && func0.call(data, "warmup_bars")){
let data5 = data.warmup_bars;
const _errs7 = errors;
if(!(((typeof data5 == "number") && (!(data5 % 1) && !isNaN(data5))) && (isFinite(data5)))){
validate467.errors = [{instancePath:instancePath+"/warmup_bars",schemaPath:"#/properties/warmup_bars/type",keyword:"type",params:{type: "integer"}}];
return false;
}
if(errors === _errs7){
if((typeof data5 == "number") && (isFinite(data5))){
if(data5 > 100000 || isNaN(data5)){
validate467.errors = [{instancePath:instancePath+"/warmup_bars",schemaPath:"#/properties/warmup_bars/maximum",keyword:"maximum",params:{comparison: "<=", limit: 100000}}];
return false;
}
else {
if(data5 < 0 || isNaN(data5)){
validate467.errors = [{instancePath:instancePath+"/warmup_bars",schemaPath:"#/properties/warmup_bars/minimum",keyword:"minimum",params:{comparison: ">=", limit: 0}}];
return false;
}
}
}
}
var valid0 = _errs7 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.as_of_ms !== undefined && func0.call(data, "as_of_ms")){
const _errs9 = errors;
if(!(validate60(data.as_of_ms, {instancePath:instancePath+"/as_of_ms",parentData:data,parentDataProperty:"as_of_ms",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate60.errors : vErrors.concat(validate60.errors);
errors = vErrors.length;
}
var valid0 = _errs9 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.content_hash !== undefined && func0.call(data, "content_hash")){
const _errs10 = errors;
if(!(validate336(data.content_hash, {instancePath:instancePath+"/content_hash",parentData:data,parentDataProperty:"content_hash",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate336.errors : vErrors.concat(validate336.errors);
errors = vErrors.length;
}
var valid0 = _errs10 === errors;
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
}
}
}
else {
validate467.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate467.errors = vErrors;
return errors === 0;
}
validate467.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema131 = {"type":"object","properties":{"as_of_ms":{"$ref":"#/$defs/Timestamp"},"last_observed_ms":{"anyOf":[{"$ref":"#/$defs/Timestamp"},{"type":"null"}]},"session_state":{"type":"string","enum":["open","closed","unknown"]},"freshness":{"type":"string","enum":["current","delayed","stale","unknown"]},"connection":{"type":"string","enum":["offline","connected","disconnected"]},"coverage":{"type":"string","enum":["observed","gapped","unknown"]},"gaps":{"type":"array","maxItems":4096,"items":{"$ref":"#/$defs/VisibleRange"}},"source_kind":{"type":"string","enum":["native_bars","snapshot_approximation","synthetic"]}},"required":["as_of_ms","last_observed_ms","session_state","freshness","connection","coverage","gaps","source_kind"],"additionalProperties":false};
const schema87 = {"type":"object","properties":{"from_ms":{"$ref":"#/$defs/Timestamp"},"to_ms":{"$ref":"#/$defs/Timestamp"}},"required":["from_ms","to_ms"],"additionalProperties":false};

function validate297(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate297.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if((((data.from_ms === undefined) || (!(func0.call(data, "from_ms")))) && (missing0 = "from_ms")) || (((data.to_ms === undefined) || (!(func0.call(data, "to_ms")))) && (missing0 = "to_ms"))){
validate297.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!((key0 === "from_ms") || (key0 === "to_ms"))){
validate297.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.from_ms !== undefined && func0.call(data, "from_ms")){
const _errs2 = errors;
if(!(validate60(data.from_ms, {instancePath:instancePath+"/from_ms",parentData:data,parentDataProperty:"from_ms",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate60.errors : vErrors.concat(validate60.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.to_ms !== undefined && func0.call(data, "to_ms")){
const _errs3 = errors;
if(!(validate60(data.to_ms, {instancePath:instancePath+"/to_ms",parentData:data,parentDataProperty:"to_ms",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate60.errors : vErrors.concat(validate60.errors);
errors = vErrors.length;
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
}
}
}
}
else {
validate297.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate297.errors = vErrors;
return errors === 0;
}
validate297.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate491(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate491.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if((((((((((data.as_of_ms === undefined) || (!(func0.call(data, "as_of_ms")))) && (missing0 = "as_of_ms")) || (((data.last_observed_ms === undefined) || (!(func0.call(data, "last_observed_ms")))) && (missing0 = "last_observed_ms"))) || (((data.session_state === undefined) || (!(func0.call(data, "session_state")))) && (missing0 = "session_state"))) || (((data.freshness === undefined) || (!(func0.call(data, "freshness")))) && (missing0 = "freshness"))) || (((data.connection === undefined) || (!(func0.call(data, "connection")))) && (missing0 = "connection"))) || (((data.coverage === undefined) || (!(func0.call(data, "coverage")))) && (missing0 = "coverage"))) || (((data.gaps === undefined) || (!(func0.call(data, "gaps")))) && (missing0 = "gaps"))) || (((data.source_kind === undefined) || (!(func0.call(data, "source_kind")))) && (missing0 = "source_kind"))){
validate491.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!((((((((key0 === "as_of_ms") || (key0 === "last_observed_ms")) || (key0 === "session_state")) || (key0 === "freshness")) || (key0 === "connection")) || (key0 === "coverage")) || (key0 === "gaps")) || (key0 === "source_kind"))){
validate491.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.as_of_ms !== undefined && func0.call(data, "as_of_ms")){
const _errs2 = errors;
if(!(validate60(data.as_of_ms, {instancePath:instancePath+"/as_of_ms",parentData:data,parentDataProperty:"as_of_ms",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate60.errors : vErrors.concat(validate60.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.last_observed_ms !== undefined && func0.call(data, "last_observed_ms")){
let data1 = data.last_observed_ms;
const _errs3 = errors;
const _errs4 = errors;
let valid1 = false;
const _errs5 = errors;
if(!(validate60(data1, {instancePath:instancePath+"/last_observed_ms",parentData:data,parentDataProperty:"last_observed_ms",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate60.errors : vErrors.concat(validate60.errors);
errors = vErrors.length;
}
var _valid0 = _errs5 === errors;
valid1 = valid1 || _valid0;
const _errs6 = errors;
if(data1 !== null){
const err0 = {instancePath:instancePath+"/last_observed_ms",schemaPath:"#/properties/last_observed_ms/anyOf/1/type",keyword:"type",params:{type: "null"}};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
var _valid0 = _errs6 === errors;
valid1 = valid1 || _valid0;
if(!valid1){
const err1 = {instancePath:instancePath+"/last_observed_ms",schemaPath:"#/properties/last_observed_ms/anyOf",keyword:"anyOf",params:{}};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
validate491.errors = vErrors;
return false;
}
else {
errors = _errs4;
if(vErrors !== null){
if(_errs4){
vErrors.length = _errs4;
}
else {
vErrors = null;
}
}
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.session_state !== undefined && func0.call(data, "session_state")){
let data2 = data.session_state;
const _errs8 = errors;
if(typeof data2 !== "string"){
validate491.errors = [{instancePath:instancePath+"/session_state",schemaPath:"#/properties/session_state/type",keyword:"type",params:{type: "string"}}];
return false;
}
if(!(((data2 === "open") || (data2 === "closed")) || (data2 === "unknown"))){
validate491.errors = [{instancePath:instancePath+"/session_state",schemaPath:"#/properties/session_state/enum",keyword:"enum",params:{allowedValues: schema131.properties.session_state.enum}}];
return false;
}
var valid0 = _errs8 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.freshness !== undefined && func0.call(data, "freshness")){
let data3 = data.freshness;
const _errs10 = errors;
if(typeof data3 !== "string"){
validate491.errors = [{instancePath:instancePath+"/freshness",schemaPath:"#/properties/freshness/type",keyword:"type",params:{type: "string"}}];
return false;
}
if(!((((data3 === "current") || (data3 === "delayed")) || (data3 === "stale")) || (data3 === "unknown"))){
validate491.errors = [{instancePath:instancePath+"/freshness",schemaPath:"#/properties/freshness/enum",keyword:"enum",params:{allowedValues: schema131.properties.freshness.enum}}];
return false;
}
var valid0 = _errs10 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.connection !== undefined && func0.call(data, "connection")){
let data4 = data.connection;
const _errs12 = errors;
if(typeof data4 !== "string"){
validate491.errors = [{instancePath:instancePath+"/connection",schemaPath:"#/properties/connection/type",keyword:"type",params:{type: "string"}}];
return false;
}
if(!(((data4 === "offline") || (data4 === "connected")) || (data4 === "disconnected"))){
validate491.errors = [{instancePath:instancePath+"/connection",schemaPath:"#/properties/connection/enum",keyword:"enum",params:{allowedValues: schema131.properties.connection.enum}}];
return false;
}
var valid0 = _errs12 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.coverage !== undefined && func0.call(data, "coverage")){
let data5 = data.coverage;
const _errs14 = errors;
if(typeof data5 !== "string"){
validate491.errors = [{instancePath:instancePath+"/coverage",schemaPath:"#/properties/coverage/type",keyword:"type",params:{type: "string"}}];
return false;
}
if(!(((data5 === "observed") || (data5 === "gapped")) || (data5 === "unknown"))){
validate491.errors = [{instancePath:instancePath+"/coverage",schemaPath:"#/properties/coverage/enum",keyword:"enum",params:{allowedValues: schema131.properties.coverage.enum}}];
return false;
}
var valid0 = _errs14 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.gaps !== undefined && func0.call(data, "gaps")){
let data6 = data.gaps;
const _errs16 = errors;
if(errors === _errs16){
if(Array.isArray(data6)){
if(data6.length > 4096){
validate491.errors = [{instancePath:instancePath+"/gaps",schemaPath:"#/properties/gaps/maxItems",keyword:"maxItems",params:{limit: 4096}}];
return false;
}
else {
var valid2 = true;
const len0 = data6.length;
for(let i0=0; i0<len0; i0++){
const _errs18 = errors;
if(!(validate297(data6[i0], {instancePath:instancePath+"/gaps/" + i0,parentData:data6,parentDataProperty:i0,rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate297.errors : vErrors.concat(validate297.errors);
errors = vErrors.length;
}
var valid2 = _errs18 === errors;
if(!valid2){
break;
}
}
}
}
else {
validate491.errors = [{instancePath:instancePath+"/gaps",schemaPath:"#/properties/gaps/type",keyword:"type",params:{type: "array"}}];
return false;
}
}
var valid0 = _errs16 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.source_kind !== undefined && func0.call(data, "source_kind")){
let data8 = data.source_kind;
const _errs19 = errors;
if(typeof data8 !== "string"){
validate491.errors = [{instancePath:instancePath+"/source_kind",schemaPath:"#/properties/source_kind/type",keyword:"type",params:{type: "string"}}];
return false;
}
if(!(((data8 === "native_bars") || (data8 === "snapshot_approximation")) || (data8 === "synthetic"))){
validate491.errors = [{instancePath:instancePath+"/source_kind",schemaPath:"#/properties/source_kind/enum",keyword:"enum",params:{allowedValues: schema131.properties.source_kind.enum}}];
return false;
}
var valid0 = _errs19 === errors;
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
}
}
}
else {
validate491.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate491.errors = vErrors;
return errors === 0;
}
validate491.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema132 = {"type":"object","properties":{"stream_id":{"$ref":"#/$defs/OpaqueId"},"epoch":{"$ref":"#/$defs/OpaqueId"},"sequence":{"$ref":"#/$defs/Revision"}},"required":["stream_id","epoch","sequence"],"additionalProperties":false};
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


function validate496(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate496.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if(((((data.stream_id === undefined) || (!(func0.call(data, "stream_id")))) && (missing0 = "stream_id")) || (((data.epoch === undefined) || (!(func0.call(data, "epoch")))) && (missing0 = "epoch"))) || (((data.sequence === undefined) || (!(func0.call(data, "sequence")))) && (missing0 = "sequence"))){
validate496.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!(((key0 === "stream_id") || (key0 === "epoch")) || (key0 === "sequence"))){
validate496.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.stream_id !== undefined && func0.call(data, "stream_id")){
const _errs2 = errors;
if(!(validate84(data.stream_id, {instancePath:instancePath+"/stream_id",parentData:data,parentDataProperty:"stream_id",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.epoch !== undefined && func0.call(data, "epoch")){
const _errs3 = errors;
if(!(validate84(data.epoch, {instancePath:instancePath+"/epoch",parentData:data,parentDataProperty:"epoch",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate84.errors : vErrors.concat(validate84.errors);
errors = vErrors.length;
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.sequence !== undefined && func0.call(data, "sequence")){
const _errs4 = errors;
if(!(validate99(data.sequence, {instancePath:instancePath+"/sequence",parentData:data,parentDataProperty:"sequence",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate99.errors : vErrors.concat(validate99.errors);
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
}
else {
validate496.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate496.errors = vErrors;
return errors === 0;
}
validate496.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema128 = {"type":"object","properties":{"snapshot_hash":{"$ref":"#/$defs/Hash"},"next_index":{"type":"integer","minimum":1,"maximum":100000},"prefix_hash":{"$ref":"#/$defs/Hash"},"previous_time_ms":{"$ref":"#/$defs/Timestamp"}},"required":["snapshot_hash","next_index","prefix_hash","previous_time_ms"],"additionalProperties":false};

function validate476(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate476.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if((((((data.snapshot_hash === undefined) || (!(func0.call(data, "snapshot_hash")))) && (missing0 = "snapshot_hash")) || (((data.next_index === undefined) || (!(func0.call(data, "next_index")))) && (missing0 = "next_index"))) || (((data.prefix_hash === undefined) || (!(func0.call(data, "prefix_hash")))) && (missing0 = "prefix_hash"))) || (((data.previous_time_ms === undefined) || (!(func0.call(data, "previous_time_ms")))) && (missing0 = "previous_time_ms"))){
validate476.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!((((key0 === "snapshot_hash") || (key0 === "next_index")) || (key0 === "prefix_hash")) || (key0 === "previous_time_ms"))){
validate476.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.snapshot_hash !== undefined && func0.call(data, "snapshot_hash")){
const _errs2 = errors;
if(!(validate336(data.snapshot_hash, {instancePath:instancePath+"/snapshot_hash",parentData:data,parentDataProperty:"snapshot_hash",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate336.errors : vErrors.concat(validate336.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.next_index !== undefined && func0.call(data, "next_index")){
let data1 = data.next_index;
const _errs3 = errors;
if(!(((typeof data1 == "number") && (!(data1 % 1) && !isNaN(data1))) && (isFinite(data1)))){
validate476.errors = [{instancePath:instancePath+"/next_index",schemaPath:"#/properties/next_index/type",keyword:"type",params:{type: "integer"}}];
return false;
}
if(errors === _errs3){
if((typeof data1 == "number") && (isFinite(data1))){
if(data1 > 100000 || isNaN(data1)){
validate476.errors = [{instancePath:instancePath+"/next_index",schemaPath:"#/properties/next_index/maximum",keyword:"maximum",params:{comparison: "<=", limit: 100000}}];
return false;
}
else {
if(data1 < 1 || isNaN(data1)){
validate476.errors = [{instancePath:instancePath+"/next_index",schemaPath:"#/properties/next_index/minimum",keyword:"minimum",params:{comparison: ">=", limit: 1}}];
return false;
}
}
}
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.prefix_hash !== undefined && func0.call(data, "prefix_hash")){
const _errs5 = errors;
if(!(validate336(data.prefix_hash, {instancePath:instancePath+"/prefix_hash",parentData:data,parentDataProperty:"prefix_hash",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate336.errors : vErrors.concat(validate336.errors);
errors = vErrors.length;
}
var valid0 = _errs5 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.previous_time_ms !== undefined && func0.call(data, "previous_time_ms")){
const _errs6 = errors;
if(!(validate60(data.previous_time_ms, {instancePath:instancePath+"/previous_time_ms",parentData:data,parentDataProperty:"previous_time_ms",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate60.errors : vErrors.concat(validate60.errors);
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
}
else {
validate476.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate476.errors = vErrors;
return errors === 0;
}
validate476.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate1655(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate1655.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(errors === 0){
if(data && typeof data == "object" && !Array.isArray(data)){
let missing0;
if((((((((((data.dataset === undefined) || (!(func0.call(data, "dataset")))) && (missing0 = "dataset")) || (((data.bars === undefined) || (!(func0.call(data, "bars")))) && (missing0 = "bars"))) || (((data.snapshot === undefined) || (!(func0.call(data, "snapshot")))) && (missing0 = "snapshot"))) || (((data.quality_state === undefined) || (!(func0.call(data, "quality_state")))) && (missing0 = "quality_state"))) || (((data.stream_position === undefined) || (!(func0.call(data, "stream_position")))) && (missing0 = "stream_position"))) || (((data.next_cursor === undefined) || (!(func0.call(data, "next_cursor")))) && (missing0 = "next_cursor"))) || (((data.total_bars === undefined) || (!(func0.call(data, "total_bars")))) && (missing0 = "total_bars"))) || (((data.offset === undefined) || (!(func0.call(data, "offset")))) && (missing0 = "offset"))){
validate1655.errors = [{instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: missing0}}];
return false;
}
else {
const _errs1 = errors;
for(const key0 of Object.keys(data)){
if(!((((((((key0 === "dataset") || (key0 === "bars")) || (key0 === "snapshot")) || (key0 === "quality_state")) || (key0 === "stream_position")) || (key0 === "next_cursor")) || (key0 === "total_bars")) || (key0 === "offset"))){
validate1655.errors = [{instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0}}];
return false;
break;
}
}
if(_errs1 === errors){
if(data.dataset !== undefined && func0.call(data, "dataset")){
const _errs2 = errors;
if(!(validate276(data.dataset, {instancePath:instancePath+"/dataset",parentData:data,parentDataProperty:"dataset",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate276.errors : vErrors.concat(validate276.errors);
errors = vErrors.length;
}
var valid0 = _errs2 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.bars !== undefined && func0.call(data, "bars")){
let data1 = data.bars;
const _errs3 = errors;
if(errors === _errs3){
if(Array.isArray(data1)){
if(data1.length > 100000){
validate1655.errors = [{instancePath:instancePath+"/bars",schemaPath:"#/properties/bars/maxItems",keyword:"maxItems",params:{limit: 100000}}];
return false;
}
else {
var valid1 = true;
const len0 = data1.length;
for(let i0=0; i0<len0; i0++){
const _errs5 = errors;
if(!(validate287(data1[i0], {instancePath:instancePath+"/bars/" + i0,parentData:data1,parentDataProperty:i0,rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate287.errors : vErrors.concat(validate287.errors);
errors = vErrors.length;
}
var valid1 = _errs5 === errors;
if(!valid1){
break;
}
}
}
}
else {
validate1655.errors = [{instancePath:instancePath+"/bars",schemaPath:"#/properties/bars/type",keyword:"type",params:{type: "array"}}];
return false;
}
}
var valid0 = _errs3 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.snapshot !== undefined && func0.call(data, "snapshot")){
const _errs6 = errors;
if(!(validate467(data.snapshot, {instancePath:instancePath+"/snapshot",parentData:data,parentDataProperty:"snapshot",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate467.errors : vErrors.concat(validate467.errors);
errors = vErrors.length;
}
var valid0 = _errs6 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.quality_state !== undefined && func0.call(data, "quality_state")){
const _errs7 = errors;
if(!(validate491(data.quality_state, {instancePath:instancePath+"/quality_state",parentData:data,parentDataProperty:"quality_state",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate491.errors : vErrors.concat(validate491.errors);
errors = vErrors.length;
}
var valid0 = _errs7 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.stream_position !== undefined && func0.call(data, "stream_position")){
let data5 = data.stream_position;
const _errs8 = errors;
const _errs9 = errors;
let valid2 = false;
const _errs10 = errors;
if(!(validate496(data5, {instancePath:instancePath+"/stream_position",parentData:data,parentDataProperty:"stream_position",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate496.errors : vErrors.concat(validate496.errors);
errors = vErrors.length;
}
var _valid0 = _errs10 === errors;
valid2 = valid2 || _valid0;
const _errs11 = errors;
if(data5 !== null){
const err0 = {instancePath:instancePath+"/stream_position",schemaPath:"#/properties/stream_position/anyOf/1/type",keyword:"type",params:{type: "null"}};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
var _valid0 = _errs11 === errors;
valid2 = valid2 || _valid0;
if(!valid2){
const err1 = {instancePath:instancePath+"/stream_position",schemaPath:"#/properties/stream_position/anyOf",keyword:"anyOf",params:{}};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
validate1655.errors = vErrors;
return false;
}
else {
errors = _errs9;
if(vErrors !== null){
if(_errs9){
vErrors.length = _errs9;
}
else {
vErrors = null;
}
}
}
var valid0 = _errs8 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.next_cursor !== undefined && func0.call(data, "next_cursor")){
let data6 = data.next_cursor;
const _errs13 = errors;
const _errs14 = errors;
let valid3 = false;
const _errs15 = errors;
if(!(validate476(data6, {instancePath:instancePath+"/next_cursor",parentData:data,parentDataProperty:"next_cursor",rootData,dynamicAnchors}))){
vErrors = vErrors === null ? validate476.errors : vErrors.concat(validate476.errors);
errors = vErrors.length;
}
var _valid1 = _errs15 === errors;
valid3 = valid3 || _valid1;
const _errs16 = errors;
if(data6 !== null){
const err2 = {instancePath:instancePath+"/next_cursor",schemaPath:"#/properties/next_cursor/anyOf/1/type",keyword:"type",params:{type: "null"}};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
var _valid1 = _errs16 === errors;
valid3 = valid3 || _valid1;
if(!valid3){
const err3 = {instancePath:instancePath+"/next_cursor",schemaPath:"#/properties/next_cursor/anyOf",keyword:"anyOf",params:{}};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
validate1655.errors = vErrors;
return false;
}
else {
errors = _errs14;
if(vErrors !== null){
if(_errs14){
vErrors.length = _errs14;
}
else {
vErrors = null;
}
}
}
var valid0 = _errs13 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.total_bars !== undefined && func0.call(data, "total_bars")){
let data7 = data.total_bars;
const _errs18 = errors;
if(!(((typeof data7 == "number") && (!(data7 % 1) && !isNaN(data7))) && (isFinite(data7)))){
validate1655.errors = [{instancePath:instancePath+"/total_bars",schemaPath:"#/properties/total_bars/type",keyword:"type",params:{type: "integer"}}];
return false;
}
if(errors === _errs18){
if((typeof data7 == "number") && (isFinite(data7))){
if(data7 > 100000 || isNaN(data7)){
validate1655.errors = [{instancePath:instancePath+"/total_bars",schemaPath:"#/properties/total_bars/maximum",keyword:"maximum",params:{comparison: "<=", limit: 100000}}];
return false;
}
else {
if(data7 < 0 || isNaN(data7)){
validate1655.errors = [{instancePath:instancePath+"/total_bars",schemaPath:"#/properties/total_bars/minimum",keyword:"minimum",params:{comparison: ">=", limit: 0}}];
return false;
}
}
}
}
var valid0 = _errs18 === errors;
}
else {
var valid0 = true;
}
if(valid0){
if(data.offset !== undefined && func0.call(data, "offset")){
let data8 = data.offset;
const _errs20 = errors;
if(!(((typeof data8 == "number") && (!(data8 % 1) && !isNaN(data8))) && (isFinite(data8)))){
validate1655.errors = [{instancePath:instancePath+"/offset",schemaPath:"#/properties/offset/type",keyword:"type",params:{type: "integer"}}];
return false;
}
if(errors === _errs20){
if((typeof data8 == "number") && (isFinite(data8))){
if(data8 > 100000 || isNaN(data8)){
validate1655.errors = [{instancePath:instancePath+"/offset",schemaPath:"#/properties/offset/maximum",keyword:"maximum",params:{comparison: "<=", limit: 100000}}];
return false;
}
else {
if(data8 < 0 || isNaN(data8)){
validate1655.errors = [{instancePath:instancePath+"/offset",schemaPath:"#/properties/offset/minimum",keyword:"minimum",params:{comparison: ">=", limit: 0}}];
return false;
}
}
}
}
var valid0 = _errs20 === errors;
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
}
}
}
else {
validate1655.errors = [{instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"}}];
return false;
}
}
validate1655.errors = vErrors;
return errors === 0;
}
validate1655.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};
